"""
Airtable API client with retry, rate limiting, and PII masking.

Handles pagination, exponential backoff, and Airtable's 5 req/sec rate limit
so callers don't have to think about it.
"""

from __future__ import annotations

import re
import time
import logging
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AirtableError(Exception):
    """Base exception for Airtable operations."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AirtableRateLimitError(AirtableError):
    """Raised when Airtable returns 429 Too Many Requests."""
    pass


class AirtableNotFoundError(AirtableError):
    """Raised when a record or table is not found (404)."""
    pass


class AirtableValidationError(AirtableError):
    """Raised when Airtable rejects a payload (422)."""
    pass


# ---------------------------------------------------------------------------
# PII masking utilities
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?1?\d{10,15}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def mask_email(email: str) -> str:
    """Mask an email address, preserving first char and domain."""
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"


def mask_phone(phone: str) -> str:
    """Mask a phone number, preserving country code prefix and last 4 digits."""
    digits_only = re.sub(r"\D", "", phone)
    if len(digits_only) <= 4:
        return "***"
    prefix = phone[:4] if phone.startswith("+") else phone[:3]
    return f"{prefix}***{digits_only[-4:]}"


def mask_ssn(ssn: str) -> str:
    """Mask an SSN, preserving last 4 digits."""
    return f"***-**-{ssn[-4:]}"


def mask_pii(data: Any) -> Any:
    """Recursively mask PII (emails, phones, SSNs) in a data structure.

    Works on strings, dicts, and lists. Returns a new object — does not
    mutate the original.
    """
    if isinstance(data, str):
        result = _SSN_RE.sub(lambda m: mask_ssn(m.group()), data)
        result = _EMAIL_RE.sub(lambda m: mask_email(m.group()), result)
        result = _PHONE_RE.sub(lambda m: mask_phone(m.group()), result)
        return result
    if isinstance(data, dict):
        return {k: mask_pii(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_pii(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.airtable.com/v0"
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds
_RATE_LIMIT_INTERVAL = 0.2  # 5 req/sec → 200ms between requests


class AirtableClient:
    """Clean wrapper around the Airtable REST API.

    Features:
        - Automatic pagination on list_records
        - Exponential backoff with 3 retries on transient errors
        - Rate limiting (5 req/sec)
        - Structured exceptions
        - PII masking utility for safe logging

    Args:
        base_id: Airtable base ID (starts with ``app``).
        api_key: Personal access token (starts with ``pat``).
        max_retries: Number of retries on transient errors. Default 3.
        rate_limit_interval: Minimum seconds between requests. Default 0.2.
    """

    def __init__(
        self,
        base_id: str,
        api_key: str,
        max_retries: int = _MAX_RETRIES,
        rate_limit_interval: float = _RATE_LIMIT_INTERVAL,
    ) -> None:
        if not base_id or not api_key:
            raise ValueError("base_id and api_key are required")

        self.base_id = base_id
        self.api_key = api_key
        self.max_retries = max_retries
        self.rate_limit_interval = rate_limit_interval
        self._last_request_time: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, table: str, record_id: Optional[str] = None) -> str:
        """Build the API URL for a table and optional record."""
        url = f"{_BASE_URL}/{self.base_id}/{requests.utils.quote(table)}"
        if record_id:
            url = f"{url}/{record_id}"
        return url

    def _throttle(self) -> None:
        """Enforce rate limit by sleeping if needed."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.rate_limit_interval:
            time.sleep(self.rate_limit_interval - elapsed)
        self._last_request_time = time.monotonic()

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        """Execute an HTTP request with retry + rate limiting.

        Retries on 429 (rate limit), 500, 502, 503, 504, and connection errors.
        Uses exponential backoff: 1s, 2s, 4s.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self._session.request(
                    method, url, params=params, json=json_body, timeout=30
                )
            except requests.ConnectionError as exc:
                last_exc = exc
                logger.warning(
                    "Connection error on attempt %d/%d: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue
            except requests.Timeout as exc:
                last_exc = exc
                logger.warning(
                    "Timeout on attempt %d/%d", attempt + 1, self.max_retries + 1
                )
                if attempt < self.max_retries:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue

            if resp.status_code == 200:
                return resp.json()

            # Rate limited — Airtable asks us to wait 30s, but we back off
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 30))
                wait = max(retry_after, _BACKOFF_BASE * (2 ** attempt))
                logger.warning(
                    "Rate limited (429). Waiting %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    self.max_retries + 1,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)
                    continue
                raise AirtableRateLimitError(
                    "Rate limit exceeded after retries",
                    status_code=429,
                    response_body=resp.json() if resp.text else None,
                )

            # Server errors — retry
            if resp.status_code in (500, 502, 503, 504):
                logger.warning(
                    "Server error %d on attempt %d/%d",
                    resp.status_code,
                    attempt + 1,
                    self.max_retries + 1,
                )
                if attempt < self.max_retries:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue

            # Client errors — don't retry
            body = resp.json() if resp.text else {}
            if resp.status_code == 404:
                raise AirtableNotFoundError(
                    f"Not found: {url}", status_code=404, response_body=body
                )
            if resp.status_code == 422:
                raise AirtableValidationError(
                    f"Validation error: {body.get('error', {}).get('message', '')}",
                    status_code=422,
                    response_body=body,
                )
            raise AirtableError(
                f"Airtable API error {resp.status_code}: {body.get('error', {}).get('message', '')}",
                status_code=resp.status_code,
                response_body=body,
            )

        # Exhausted retries on connection/timeout errors
        raise AirtableError(f"Request failed after {self.max_retries + 1} attempts: {last_exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_records(
        self,
        table: str,
        filter_formula: Optional[str] = None,
        max_records: Optional[int] = None,
        sort: Optional[List[Dict[str, str]]] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List records from a table with automatic pagination.

        Args:
            table: Table name.
            filter_formula: Airtable formula to filter records.
            max_records: Maximum number of records to return.
            sort: List of sort objects, e.g. ``[{"field": "Name", "direction": "asc"}]``.
            fields: List of field names to include in the response.

        Returns:
            List of record dicts with ``id``, ``fields``, and ``createdTime``.
        """
        params: Dict[str, Any] = {}
        if filter_formula:
            params["filterByFormula"] = filter_formula
        if max_records:
            params["maxRecords"] = max_records
        if sort:
            for i, s in enumerate(sort):
                params[f"sort[{i}][field]"] = s["field"]
                params[f"sort[{i}][direction]"] = s.get("direction", "asc")
        if fields:
            params["fields[]"] = fields

        url = self._url(table)
        all_records: List[Dict[str, Any]] = []

        while True:
            data = self._request("GET", url, params=params)
            all_records.extend(data.get("records", []))

            offset = data.get("offset")
            if not offset:
                break
            params["offset"] = offset

            if max_records and len(all_records) >= max_records:
                all_records = all_records[:max_records]
                break

        return all_records

    def get_record(self, table: str, record_id: str) -> Dict[str, Any]:
        """Get a single record by ID.

        Args:
            table: Table name.
            record_id: Airtable record ID (starts with ``rec``).

        Returns:
            Record dict with ``id``, ``fields``, and ``createdTime``.

        Raises:
            AirtableNotFoundError: If the record doesn't exist.
        """
        return self._request("GET", self._url(table, record_id))

    def create_record(self, table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a single record.

        Args:
            table: Table name.
            fields: Dict of field name to value.

        Returns:
            The created record dict.
        """
        if not fields:
            raise ValueError("fields cannot be empty")
        return self._request(
            "POST", self._url(table), json_body={"fields": fields}
        )

    def create_records(self, table: str, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create multiple records in batches of 10 (Airtable's limit).

        Args:
            table: Table name.
            records: List of field dicts.

        Returns:
            List of created record dicts.
        """
        if not records:
            return []

        created: List[Dict[str, Any]] = []
        for i in range(0, len(records), 10):
            batch = [{"fields": r} for r in records[i : i + 10]]
            data = self._request(
                "POST", self._url(table), json_body={"records": batch}
            )
            created.extend(data.get("records", []))
        return created

    def update_record(
        self, table: str, record_id: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Partial update (PATCH) a single record.

        Args:
            table: Table name.
            record_id: Record ID.
            fields: Fields to update.

        Returns:
            The updated record dict.
        """
        if not fields:
            raise ValueError("fields cannot be empty")
        return self._request(
            "PATCH",
            self._url(table, record_id),
            json_body={"fields": fields},
        )

    def update_records(
        self, table: str, records: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Batch update records. Each item must have ``id`` and ``fields``.

        Args:
            table: Table name.
            records: List of dicts with ``id`` and ``fields`` keys.

        Returns:
            List of updated record dicts.
        """
        if not records:
            return []

        updated: List[Dict[str, Any]] = []
        for i in range(0, len(records), 10):
            batch = records[i : i + 10]
            data = self._request(
                "PATCH", self._url(table), json_body={"records": list(batch)}
            )
            updated.extend(data.get("records", []))
        return updated

    def delete_record(self, table: str, record_id: str) -> Dict[str, Any]:
        """Delete a single record.

        Args:
            table: Table name.
            record_id: Record ID.

        Returns:
            Deletion confirmation dict.

        Raises:
            AirtableNotFoundError: If the record doesn't exist.
        """
        return self._request("DELETE", self._url(table, record_id))

    def delete_records(self, table: str, record_ids: Sequence[str]) -> List[Dict[str, Any]]:
        """Batch delete records (up to 10 at a time).

        Args:
            table: Table name.
            record_ids: List of record IDs to delete.

        Returns:
            List of deletion confirmation dicts.
        """
        if not record_ids:
            return []

        deleted: List[Dict[str, Any]] = []
        for i in range(0, len(record_ids), 10):
            batch = record_ids[i : i + 10]
            params = {"records[]": list(batch)}
            data = self._request("DELETE", self._url(table), params=params)
            deleted.extend(data.get("records", []))
        return deleted

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def mask_pii(data: Any) -> Any:
        """Mask PII in a data structure. Convenience wrapper around module-level mask_pii."""
        return mask_pii(data)
