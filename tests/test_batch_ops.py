"""Unit tests for batch_ops chunking helper."""

from unittest.mock import MagicMock

from src.batch_ops import batch_create


def _make_client():
    client = MagicMock()
    # Echo each created record back with a synthetic id
    client.create_record.side_effect = lambda table, fields: {"id": f"rec-{fields['n']}", "fields": fields}
    return client


def test_batch_create_empty():
    client = _make_client()
    assert batch_create(client, "T", []) == []
    client.create_record.assert_not_called()


def test_batch_create_returns_all_records():
    client = _make_client()
    records = [{"n": i} for i in range(25)]

    results = batch_create(client, "T", records)

    assert len(results) == 25
    assert client.create_record.call_count == 25
    assert results[0]["id"] == "rec-0"
    assert results[-1]["id"] == "rec-24"


def test_batch_create_respects_default_chunk_size():
    # Default chunk size is 10 (Airtable's batch limit); 25 records -> 3 chunks.
    # The helper still issues one create_record call per record, but iterates
    # in chunks — verify all records flow through regardless of chunk boundary.
    client = _make_client()
    records = [{"n": i} for i in range(25)]
    results = batch_create(client, "T", records, chunk_size=10)
    assert [r["fields"]["n"] for r in results] == list(range(25))
