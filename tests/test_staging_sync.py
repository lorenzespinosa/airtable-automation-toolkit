"""Unit tests for StagingSync — checksum upsert, conflict detection, and sync cycle."""

from unittest.mock import patch

import pytest

from src.staging_sync import StagingSync, _compute_checksum


@pytest.fixture
def sync():
    """Create a StagingSync instance with a mocked AirtableClient."""
    with patch("src.staging_sync.AirtableClient") as MockClient:
        instance = MockClient.return_value
        engine = StagingSync(base_id="appTEST", api_key="patTEST", table_name="Staging")
        engine._mock_client = instance
        yield engine


# ---------------------------------------------------------------------------
# Checksum helper
# ---------------------------------------------------------------------------


class TestComputeChecksum:
    def test_deterministic(self):
        a = _compute_checksum({"name": "Alice", "status": "open"})
        b = _compute_checksum({"status": "open", "name": "Alice"})
        # Key order must not matter (sort_keys=True)
        assert a == b

    def test_changes_with_data(self):
        a = _compute_checksum({"name": "Alice"})
        b = _compute_checksum({"name": "Bob"})
        assert a != b

    def test_length_is_truncated(self):
        assert len(_compute_checksum({"x": 1})) == 16


# ---------------------------------------------------------------------------
# push_to_staging
# ---------------------------------------------------------------------------


class TestPushToStaging:
    def test_empty_records_returns_empty(self, sync):
        assert sync.push_to_staging([]) == []
        sync._mock_client.list_records.assert_not_called()

    def test_missing_external_id_raises(self, sync):
        with pytest.raises(ValueError):
            sync.push_to_staging([{"name": "no id"}])

    def test_creates_new_record_when_absent(self, sync):
        sync._mock_client.list_records.return_value = []  # not in staging yet
        sync._mock_client.create_record.return_value = {"id": "recNEW", "fields": {}}

        result = sync.push_to_staging(
            [{"external_id": "LM-1", "name": "Case Alpha"}], source="lawmatics"
        )

        assert result[0]["id"] == "recNEW"
        sync._mock_client.create_record.assert_called_once()
        fields = sync._mock_client.create_record.call_args[0][1]
        assert fields["external_id"] == "LM-1"
        assert fields["source"] == "lawmatics"
        assert fields["sync_status"] == StagingSync.STATUS_PENDING
        assert fields["checksum"] == _compute_checksum({"name": "Case Alpha"})

    def test_skips_unchanged_record(self, sync):
        checksum = _compute_checksum({"name": "Case Alpha"})
        sync._mock_client.list_records.return_value = [
            {"id": "recOLD", "fields": {"checksum": checksum}}
        ]

        result = sync.push_to_staging([{"external_id": "LM-1", "name": "Case Alpha"}])

        # Unchanged — no create or update, existing record returned
        assert result[0]["id"] == "recOLD"
        sync._mock_client.create_record.assert_not_called()
        sync._mock_client.update_record.assert_not_called()

    def test_updates_changed_record(self, sync):
        sync._mock_client.list_records.return_value = [
            {"id": "recOLD", "fields": {"checksum": "stale-checksum"}}
        ]
        sync._mock_client.update_record.return_value = {"id": "recOLD", "fields": {}}

        result = sync.push_to_staging([{"external_id": "LM-1", "name": "Case Beta"}])

        assert result[0]["id"] == "recOLD"
        sync._mock_client.update_record.assert_called_once()
        sync._mock_client.create_record.assert_not_called()


# ---------------------------------------------------------------------------
# pull_from_staging
# ---------------------------------------------------------------------------


class TestPullFromStaging:
    def test_parses_data_field(self, sync):
        sync._mock_client.list_records.return_value = [
            {
                "id": "rec1",
                "fields": {
                    "external_id": "LM-1",
                    "data": '{"name": "Case Alpha"}',
                    "sync_status": "pending",
                    "source": "lawmatics",
                    "checksum": "abc",
                },
            }
        ]

        result = sync.pull_from_staging(status="pending")

        assert result[0]["record_id"] == "rec1"
        assert result[0]["external_id"] == "LM-1"
        assert result[0]["data"] == {"name": "Case Alpha"}
        assert result[0]["sync_status"] == "pending"

    def test_malformed_json_is_wrapped(self, sync):
        sync._mock_client.list_records.return_value = [
            {"id": "rec1", "fields": {"data": "not-json{", "external_id": "LM-2"}}
        ]

        result = sync.pull_from_staging()
        assert result[0]["data"] == {"_raw": "not-json{"}

    def test_combines_filter_and_status_with_and(self, sync):
        sync._mock_client.list_records.return_value = []

        sync.pull_from_staging(filter_formula="{source}='lawmatics'", status="pending")

        formula = sync._mock_client.list_records.call_args[1]["filter_formula"]
        assert formula.startswith("AND(")
        assert "{source}='lawmatics'" in formula
        assert "{sync_status}='pending'" in formula


# ---------------------------------------------------------------------------
# mark helpers
# ---------------------------------------------------------------------------


class TestMarkHelpers:
    def test_mark_synced(self, sync):
        sync._mock_client.update_record.return_value = {"id": "rec1", "fields": {}}
        sync.mark_synced("rec1")
        fields = sync._mock_client.update_record.call_args[0][2]
        assert fields["sync_status"] == StagingSync.STATUS_SYNCED
        assert "last_synced" in fields

    def test_mark_error(self, sync):
        sync._mock_client.update_record.return_value = {"id": "rec1", "fields": {}}
        sync.mark_error("rec1", "boom")
        fields = sync._mock_client.update_record.call_args[0][2]
        assert fields["sync_status"] == StagingSync.STATUS_ERROR


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    def test_detects_checksum_mismatch(self, sync):
        sync._mock_client.list_records.return_value = [
            {"id": "recX", "fields": {"checksum": "different-checksum"}}
        ]
        sync._mock_client.update_record.return_value = {"id": "recX", "fields": {}}

        conflicts = sync.detect_conflicts([{"external_id": "LM-1", "name": "Changed"}])

        assert len(conflicts) == 1
        assert conflicts[0]["external_id"] == "LM-1"
        assert conflicts[0]["staging_record_id"] == "recX"
        # Conflict status written back to Airtable
        fields = sync._mock_client.update_record.call_args[0][2]
        assert fields["sync_status"] == StagingSync.STATUS_CONFLICT

    def test_no_conflict_when_checksum_matches(self, sync):
        checksum = _compute_checksum({"name": "Same"})
        sync._mock_client.list_records.return_value = [
            {"id": "recX", "fields": {"checksum": checksum}}
        ]

        conflicts = sync.detect_conflicts([{"external_id": "LM-1", "name": "Same"}])
        assert conflicts == []

    def test_no_conflict_when_record_absent(self, sync):
        sync._mock_client.list_records.return_value = []
        conflicts = sync.detect_conflicts([{"external_id": "LM-1", "name": "New"}])
        assert conflicts == []

    def test_skips_records_without_external_id(self, sync):
        conflicts = sync.detect_conflicts([{"name": "no id"}])
        assert conflicts == []
        sync._mock_client.list_records.assert_not_called()


# ---------------------------------------------------------------------------
# sync (full cycle)
# ---------------------------------------------------------------------------


class TestSyncCycle:
    def test_full_sync_marks_synced(self, sync):
        source = [{"external_id": "LM-1", "name": "Case Alpha"}]

        # push_to_staging: not present -> create
        # detect_conflicts: not present -> no conflict
        sync._mock_client.list_records.side_effect = [
            [],  # push_to_staging existence check
            [],  # detect_conflicts existence check
            [   # pull_from_staging (pending)
                {
                    "id": "recP",
                    "fields": {
                        "external_id": "LM-1",
                        "data": '{"name": "Case Alpha"}',
                        "sync_status": "pending",
                        "source": "src",
                        "checksum": "abc",
                    },
                }
            ],
        ]
        sync._mock_client.create_record.return_value = {"id": "recP", "fields": {}}
        sync._mock_client.update_record.return_value = {"id": "recP", "fields": {}}

        # Destination echoes records back -> treated as synced
        def dest(records):
            return records

        summary = sync.sync(
            source_callable=lambda: source,
            dest_callable=dest,
            source_name="src",
        )

        assert summary["source_records"] == 1
        assert summary["synced"] == 1
        assert summary["errors"] == 0
        assert summary["conflicts"] == 0

    def test_sync_marks_error_when_destination_raises(self, sync):
        source = [{"external_id": "LM-1", "name": "Case Alpha"}]
        sync._mock_client.list_records.side_effect = [
            [],  # push existence check
            [],  # conflict existence check
            [   # pull pending
                {
                    "id": "recP",
                    "fields": {
                        "external_id": "LM-1",
                        "data": "{}",
                        "sync_status": "pending",
                    },
                }
            ],
        ]
        sync._mock_client.create_record.return_value = {"id": "recP", "fields": {}}
        sync._mock_client.update_record.return_value = {"id": "recP", "fields": {}}

        def dest(records):
            raise RuntimeError("destination down")

        summary = sync.sync(
            source_callable=lambda: source,
            dest_callable=dest,
            source_name="src",
        )

        assert summary["errors"] == 1
        assert summary["synced"] == 0
