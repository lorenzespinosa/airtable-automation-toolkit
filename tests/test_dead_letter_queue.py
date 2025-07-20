"""Unit tests for DeadLetterQueue."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.dead_letter_queue import DeadLetterQueue


@pytest.fixture
def dlq():
    """Create a DLQ instance with mocked client."""
    with patch("src.dead_letter_queue.AirtableClient") as MockClient:
        instance = MockClient.return_value
        queue = DeadLetterQueue(base_id="appTEST", api_key="patTEST", table_name="DLQ")
        queue._mock_client = instance
        yield queue


class TestEnqueue:
    def test_enqueue_creates_record(self, dlq):
        dlq._mock_client.create_record.return_value = {
            "id": "recDLQ1",
            "fields": {"Source": "test", "Status": "Pending"},
        }

        result = dlq.enqueue(
            source="n8n-webhook",
            error="Connection timeout",
            payload={"lead_id": 123},
        )

        assert result["id"] == "recDLQ1"
        call_args = dlq._mock_client.create_record.call_args
        fields = call_args[0][1]
        assert fields["Source"] == "n8n-webhook"
        assert fields["Error"] == "Connection timeout"
        assert fields["Status"] == "Pending"
        assert fields["RetryCount"] == 0
        assert json.loads(fields["Payload"]) == {"lead_id": 123}

    def test_enqueue_with_extra_fields(self, dlq):
        dlq._mock_client.create_record.return_value = {"id": "recDLQ2", "fields": {}}

        dlq.enqueue(
            source="test",
            error="err",
            payload={},
            extra_fields={"Priority": "High"},
        )

        call_args = dlq._mock_client.create_record.call_args
        fields = call_args[0][1]
        assert fields["Priority"] == "High"


class TestDequeue:
    def test_dequeue_returns_oldest_pending(self, dlq):
        record = {"id": "recOLD", "fields": {"Status": "Pending", "Source": "test"}}
        dlq._mock_client.list_records.return_value = [record]
        dlq._mock_client.update_record.return_value = {
            "id": "recOLD",
            "fields": {"Status": "Processing"},
        }

        result = dlq.dequeue()
        assert result["id"] == "recOLD"

        # Verify it was marked as Processing
        update_args = dlq._mock_client.update_record.call_args
        assert update_args[0][2]["Status"] == "Processing"

    def test_dequeue_empty_queue(self, dlq):
        dlq._mock_client.list_records.return_value = []
        assert dlq.dequeue() is None


class TestRetry:
    def test_retry_increments_count(self, dlq):
        dlq._mock_client.get_record.return_value = {
            "id": "rec1",
            "fields": {"RetryCount": 2, "Status": "Processing"},
        }
        dlq._mock_client.update_record.return_value = {
            "id": "rec1",
            "fields": {"RetryCount": 3, "Status": "Retried"},
        }

        result = dlq.retry("rec1")
        update_args = dlq._mock_client.update_record.call_args
        fields = update_args[0][2]
        assert fields["RetryCount"] == 3
        assert fields["Status"] == "Retried"


class TestResolve:
    def test_resolve_sets_status(self, dlq):
        dlq._mock_client.update_record.return_value = {
            "id": "rec1",
            "fields": {"Status": "Resolved"},
        }

        dlq.resolve("rec1")
        update_args = dlq._mock_client.update_record.call_args
        assert update_args[0][2]["Status"] == "Resolved"


class TestListPending:
    def test_list_pending(self, dlq):
        dlq._mock_client.list_records.return_value = [
            {"id": "rec1", "fields": {"Status": "Pending"}},
            {"id": "rec2", "fields": {"Status": "Pending"}},
        ]

        result = dlq.list_pending()
        assert len(result) == 2

        # Verify correct filter was used
        call_args = dlq._mock_client.list_records.call_args
        assert "Pending" in call_args[1].get("filter_formula", call_args[0][0] if call_args[0] else "")


class TestGetPayload:
    def test_get_payload_parses_json(self, dlq):
        dlq._mock_client.get_record.return_value = {
            "id": "rec1",
            "fields": {"Payload": '{"lead_id": 42, "name": "Test"}'},
        }

        payload = dlq.get_payload("rec1")
        assert payload["lead_id"] == 42
        assert payload["name"] == "Test"

    def test_get_payload_empty(self, dlq):
        dlq._mock_client.get_record.return_value = {
            "id": "rec1",
            "fields": {},
        }

        payload = dlq.get_payload("rec1")
        assert payload == {}
