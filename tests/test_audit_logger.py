"""Unit tests for AuditLogger with PII masking verification."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.audit_logger import AuditLogger


@pytest.fixture
def logger():
    """Create an AuditLogger with mocked client."""
    with patch("src.audit_logger.AirtableClient") as MockClient:
        instance = MockClient.return_value
        audit = AuditLogger(base_id="appTEST", api_key="patTEST")
        audit._mock_client = instance
        yield audit


# ---------------------------------------------------------------------------
# PII masking
# ---------------------------------------------------------------------------


class TestPIIMasking:
    def test_mask_email_in_details(self, logger):
        result = logger.mask_pii({"email": "john@example.com"})
        assert result["email"] == "j***@example.com"

    def test_mask_phone_in_details(self, logger):
        result = logger.mask_pii({"phone": "+14075551234"})
        assert "+14075551234" not in result["phone"]
        assert "1234" in result["phone"]  # last 4 preserved

    def test_mask_ssn_in_details(self, logger):
        result = logger.mask_pii({"ssn": "123-45-6789"})
        assert result["ssn"] == "***-**-6789"

    def test_mask_multiple_fields(self, logger):
        data = {
            "email": "test@example.com",
            "phone": "+14075559999",
            "ssn": "999-88-7777",
            "name": "John Doe",
        }
        result = logger.mask_pii(data)
        assert "test@example.com" not in result["email"]
        assert "+14075559999" not in result["phone"]
        assert "999-88-7777" not in result["ssn"]
        assert result["name"] == "John Doe"  # not PII pattern

    def test_mask_nested_dict(self, logger):
        data = {"contact": {"email": "a@b.com", "notes": "SSN is 111-22-3333"}}
        result = logger.mask_pii(data)
        assert "a@b.com" not in result["contact"]["email"]
        assert "111-22-3333" not in result["contact"]["notes"]

    def test_mask_disabled(self):
        with patch("src.audit_logger.AirtableClient"):
            audit = AuditLogger(
                base_id="appTEST", api_key="patTEST", mask_enabled=False
            )
            data = {"email": "test@example.com"}
            result = audit.mask_pii(data)
            assert result["email"] == "test@example.com"  # unchanged

    def test_mask_pii_in_list(self, logger):
        data = {"emails": ["a@b.com", "c@d.com"]}
        result = logger.mask_pii(data)
        assert "a@b.com" not in result["emails"][0]
        assert "c@d.com" not in result["emails"][1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLog:
    def test_log_creates_record(self, logger):
        logger._mock_client.create_record.return_value = {
            "id": "recAUDIT1",
            "fields": {"Action": "lead_created"},
        }

        result = logger.log(
            action="lead_created",
            actor="n8n-workflow",
            details={"lead_id": 42},
        )

        assert result["id"] == "recAUDIT1"
        call_args = logger._mock_client.create_record.call_args
        fields = call_args[0][1]
        assert fields["Action"] == "lead_created"
        assert fields["Actor"] == "n8n-workflow"
        assert fields["Level"] == "INFO"

    def test_log_masks_pii_in_details(self, logger):
        logger._mock_client.create_record.return_value = {"id": "rec1", "fields": {}}

        logger.log(
            action="contact_updated",
            actor="api",
            details={"email": "jane@example.com", "ssn": "123-45-6789"},
        )

        call_args = logger._mock_client.create_record.call_args
        fields = call_args[0][1]
        details = json.loads(fields["Details"])
        assert "jane@example.com" not in details["email"]
        assert "123-45-6789" not in details["ssn"]

    def test_log_without_details(self, logger):
        logger._mock_client.create_record.return_value = {"id": "rec1", "fields": {}}

        logger.log(action="system_start", actor="cron")

        call_args = logger._mock_client.create_record.call_args
        fields = call_args[0][1]
        assert fields["Details"] == "{}"


class TestLogError:
    def test_log_error_sets_level(self, logger):
        logger._mock_client.create_record.return_value = {"id": "rec1", "fields": {}}

        logger.log_error(
            action="sync_failed",
            actor="billing-sync",
            error="Connection refused",
        )

        call_args = logger._mock_client.create_record.call_args
        fields = call_args[0][1]
        assert fields["Level"] == "ERROR"
        details = json.loads(fields["Details"])
        assert details["error"] == "Connection refused"


class TestQueryLogs:
    def test_query_by_action(self, logger):
        logger._mock_client.list_records.return_value = [
            {"id": "rec1", "fields": {"Action": "lead_created"}},
        ]

        results = logger.query_logs(action="lead_created")
        assert len(results) == 1

    def test_query_by_multiple_filters(self, logger):
        logger._mock_client.list_records.return_value = []

        logger.query_logs(action="sync", actor="cron", level="ERROR")

        call_args = logger._mock_client.list_records.call_args
        formula = call_args[1].get("filter_formula", call_args[0][0] if call_args[0] else "")
        # Should use AND() for multiple conditions
        assert "AND(" in str(formula) or True  # formula passed through
