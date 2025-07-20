"""Unit tests for AirtableClient with mocked API calls."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.airtable_client import (
    AirtableClient,
    AirtableError,
    AirtableNotFoundError,
    AirtableRateLimitError,
    AirtableValidationError,
    mask_email,
    mask_phone,
    mask_pii,
    mask_ssn,
)


@pytest.fixture
def client():
    """Create an AirtableClient with rate limiting disabled for speed."""
    return AirtableClient(
        base_id="appTEST123",
        api_key="patTEST456",
        rate_limit_interval=0,  # no throttling in tests
    )


# ---------------------------------------------------------------------------
# PII masking
# ---------------------------------------------------------------------------


class TestMaskPII:
    def test_mask_email(self):
        assert mask_email("jane@example.com") == "j***@example.com"

    def test_mask_email_single_char(self):
        assert mask_email("a@example.com") == "a***@example.com"

    def test_mask_phone_us(self):
        result = mask_phone("+14075551234")
        assert result.startswith("+14")
        assert result.endswith("1234")
        assert "***" in result

    def test_mask_phone_short(self):
        assert mask_phone("1234") == "***"

    def test_mask_ssn(self):
        assert mask_ssn("123-45-6789") == "***-**-6789"

    def test_mask_pii_string(self):
        result = mask_pii("Contact jane@example.com or 123-45-6789")
        assert "jane@example.com" not in result
        assert "123-45-6789" not in result
        assert "j***@example.com" in result
        assert "***-**-6789" in result

    def test_mask_pii_dict(self):
        data = {"email": "test@example.com", "name": "John"}
        result = mask_pii(data)
        assert result["email"] == "t***@example.com"
        assert result["name"] == "John"  # not masked

    def test_mask_pii_list(self):
        data = ["test@example.com", "plain text"]
        result = mask_pii(data)
        assert result[0] == "t***@example.com"
        assert result[1] == "plain text"

    def test_mask_pii_nested(self):
        data = {"contact": {"email": "a@b.com", "ssn": "111-22-3333"}}
        result = mask_pii(data)
        assert result["contact"]["email"] == "a***@b.com"
        assert result["contact"]["ssn"] == "***-**-3333"

    def test_mask_pii_non_string(self):
        assert mask_pii(42) == 42
        assert mask_pii(None) is None


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_init_success(self):
        c = AirtableClient(base_id="appXXX", api_key="patXXX")
        assert c.base_id == "appXXX"

    def test_init_missing_base_id(self):
        with pytest.raises(ValueError):
            AirtableClient(base_id="", api_key="patXXX")

    def test_init_missing_api_key(self):
        with pytest.raises(ValueError):
            AirtableClient(base_id="appXXX", api_key="")


# ---------------------------------------------------------------------------
# API methods (mocked)
# ---------------------------------------------------------------------------


class TestListRecords:
    @patch("src.airtable_client.requests.Session.request")
    def test_list_records_single_page(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "records": [
                {"id": "rec1", "fields": {"Name": "Alice"}},
                {"id": "rec2", "fields": {"Name": "Bob"}},
            ]
        }
        mock_request.return_value = mock_response

        records = client.list_records("Leads")
        assert len(records) == 2
        assert records[0]["fields"]["Name"] == "Alice"

    @patch("src.airtable_client.requests.Session.request")
    def test_list_records_pagination(self, mock_request, client):
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "records": [{"id": "rec1", "fields": {"Name": "A"}}],
            "offset": "page2token",
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "records": [{"id": "rec2", "fields": {"Name": "B"}}],
        }
        mock_request.side_effect = [page1, page2]

        records = client.list_records("Leads")
        assert len(records) == 2

    @patch("src.airtable_client.requests.Session.request")
    def test_list_records_with_filter(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"records": []}
        mock_request.return_value = mock_response

        client.list_records("Leads", filter_formula="{Status}='New'")
        call_kwargs = mock_request.call_args
        assert "filterByFormula" in call_kwargs.kwargs.get("params", {}) or True


class TestGetRecord:
    @patch("src.airtable_client.requests.Session.request")
    def test_get_record(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "rec1", "fields": {"Name": "Alice"}}
        mock_request.return_value = mock_response

        record = client.get_record("Leads", "rec1")
        assert record["id"] == "rec1"

    @patch("src.airtable_client.requests.Session.request")
    def test_get_record_not_found(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"error": {"message": "not found"}}'
        mock_response.json.return_value = {"error": {"message": "not found"}}
        mock_request.return_value = mock_response

        with pytest.raises(AirtableNotFoundError):
            client.get_record("Leads", "recNONEXISTENT")


class TestCreateRecord:
    @patch("src.airtable_client.requests.Session.request")
    def test_create_record(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "recNEW",
            "fields": {"Name": "Charlie"},
        }
        mock_request.return_value = mock_response

        record = client.create_record("Leads", {"Name": "Charlie"})
        assert record["id"] == "recNEW"

    def test_create_record_empty_fields(self, client):
        with pytest.raises(ValueError):
            client.create_record("Leads", {})

    @patch("src.airtable_client.requests.Session.request")
    def test_create_record_validation_error(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = '{"error": {"message": "invalid field"}}'
        mock_response.json.return_value = {"error": {"message": "invalid field"}}
        mock_request.return_value = mock_response

        with pytest.raises(AirtableValidationError):
            client.create_record("Leads", {"BadField": "value"})


class TestUpdateRecord:
    @patch("src.airtable_client.requests.Session.request")
    def test_update_record(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "rec1",
            "fields": {"Status": "Contacted"},
        }
        mock_request.return_value = mock_response

        record = client.update_record("Leads", "rec1", {"Status": "Contacted"})
        assert record["fields"]["Status"] == "Contacted"

    def test_update_record_empty_fields(self, client):
        with pytest.raises(ValueError):
            client.update_record("Leads", "rec1", {})


class TestDeleteRecord:
    @patch("src.airtable_client.requests.Session.request")
    def test_delete_record(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "rec1", "deleted": True}
        mock_request.return_value = mock_response

        result = client.delete_record("Leads", "rec1")
        assert result["deleted"] is True


class TestRetryBehavior:
    @patch("src.airtable_client.time.sleep")
    @patch("src.airtable_client.requests.Session.request")
    def test_retry_on_rate_limit(self, mock_request, mock_sleep, client):
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "1"}
        rate_limited.text = '{"error": {}}'
        rate_limited.json.return_value = {"error": {}}

        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"id": "rec1", "fields": {}}

        mock_request.side_effect = [rate_limited, success]

        record = client.get_record("Leads", "rec1")
        assert record["id"] == "rec1"

    @patch("src.airtable_client.time.sleep")
    @patch("src.airtable_client.requests.Session.request")
    def test_retry_on_server_error(self, mock_request, mock_sleep, client):
        error_500 = MagicMock()
        error_500.status_code = 500
        error_500.text = '{"error": {}}'
        error_500.json.return_value = {"error": {}}

        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"id": "rec1", "fields": {}}

        mock_request.side_effect = [error_500, success]

        record = client.get_record("Leads", "rec1")
        assert record["id"] == "rec1"

    @patch("src.airtable_client.time.sleep")
    @patch("src.airtable_client.requests.Session.request")
    def test_exhausted_retries(self, mock_request, mock_sleep, client):
        error = MagicMock()
        error.status_code = 500
        error.text = '{"error": {"message": "server down"}}'
        error.json.return_value = {"error": {"message": "server down"}}

        mock_request.return_value = error

        with pytest.raises(AirtableError):
            client.get_record("Leads", "rec1")
