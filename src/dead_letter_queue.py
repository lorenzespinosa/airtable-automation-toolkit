"""
Dead letter queue backed by Airtable.

Captures failed webhook payloads, API errors, and other transient failures
so they can be inspected in the Airtable UI and retried programmatically.

Expected Airtable table columns:
    - Source (Single line text)
    - Error (Long text)
    - Payload (Long text — JSON string)
    - Status (Single select: Pending, Processing, Retried, Resolved)
    - Timestamp (Date/time)
    - RetryCount (Number)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.airtable_client import AirtableClient

logger = logging.getLogger(__name__)


class DeadLetterQueue:
    """Dead letter queue using an Airtable table as the backend.

    Args:
        base_id: Airtable base ID.
        api_key: Airtable personal access token.
        table_name: Name of the DLQ table in Airtable. Default ``DeadLetterQueue``.
    """

    STATUS_PENDING = "Pending"
    STATUS_PROCESSING = "Processing"
    STATUS_RETRIED = "Retried"
    STATUS_RESOLVED = "Resolved"

    def __init__(
        self,
        base_id: str,
        api_key: str,
        table_name: str = "DeadLetterQueue",
    ) -> None:
        self._client = AirtableClient(base_id=base_id, api_key=api_key)
        self._table = table_name

    def enqueue(
        self,
        source: str,
        error: str,
        payload: Any,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a failed item to the dead letter queue.

        Args:
            source: Identifier for the system or workflow that failed
                    (e.g. ``"n8n-intake-webhook"``).
            error: Error message or traceback.
            payload: The original payload that failed. Will be JSON-serialized.
            extra_fields: Optional additional fields to store on the record.

        Returns:
            The created Airtable record.
        """
        fields: Dict[str, Any] = {
            "Source": source,
            "Error": str(error),
            "Payload": json.dumps(payload, default=str),
            "Status": self.STATUS_PENDING,
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "RetryCount": 0,
        }
        if extra_fields:
            fields.update(extra_fields)

        record = self._client.create_record(self._table, fields)
        logger.info("DLQ enqueue: source=%s record_id=%s", source, record["id"])
        return record

    def dequeue(self) -> Optional[Dict[str, Any]]:
        """Get and lock the oldest pending item.

        Sets the status to ``Processing`` so other workers don't pick it up.

        Returns:
            The record dict, or ``None`` if the queue is empty.
        """
        records = self._client.list_records(
            self._table,
            filter_formula=f"{{Status}}='{self.STATUS_PENDING}'",
            max_records=1,
            sort=[{"field": "Timestamp", "direction": "asc"}],
        )
        if not records:
            return None

        record = records[0]
        self._client.update_record(
            self._table, record["id"], {"Status": self.STATUS_PROCESSING}
        )
        logger.info("DLQ dequeue: record_id=%s", record["id"])
        return record

    def retry(self, record_id: str) -> Dict[str, Any]:
        """Mark a record for retry by resetting its status to Pending.

        Increments the retry count.

        Args:
            record_id: Airtable record ID.

        Returns:
            The updated record.
        """
        record = self._client.get_record(self._table, record_id)
        current_count = record.get("fields", {}).get("RetryCount", 0)

        updated = self._client.update_record(
            self._table,
            record_id,
            {
                "Status": self.STATUS_RETRIED,
                "RetryCount": current_count + 1,
                "Timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info("DLQ retry: record_id=%s retry_count=%d", record_id, current_count + 1)
        return updated

    def resolve(self, record_id: str) -> Dict[str, Any]:
        """Mark a record as resolved.

        Args:
            record_id: Airtable record ID.

        Returns:
            The updated record.
        """
        updated = self._client.update_record(
            self._table, record_id, {"Status": self.STATUS_RESOLVED}
        )
        logger.info("DLQ resolve: record_id=%s", record_id)
        return updated

    def list_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all pending items in the queue.

        Args:
            limit: Maximum number of records to return. Default 100.

        Returns:
            List of pending record dicts, oldest first.
        """
        return self._client.list_records(
            self._table,
            filter_formula=f"{{Status}}='{self.STATUS_PENDING}'",
            max_records=limit,
            sort=[{"field": "Timestamp", "direction": "asc"}],
        )

    def list_all(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List DLQ items, optionally filtered by status.

        Args:
            status: Filter by status (Pending, Processing, Retried, Resolved).
            limit: Maximum records. Default 100.

        Returns:
            List of records.
        """
        formula = f"{{Status}}='{status}'" if status else None
        return self._client.list_records(
            self._table,
            filter_formula=formula,
            max_records=limit,
            sort=[{"field": "Timestamp", "direction": "desc"}],
        )

    def get_payload(self, record_id: str) -> Any:
        """Retrieve and parse the JSON payload from a DLQ record.

        Args:
            record_id: Airtable record ID.

        Returns:
            The parsed payload object.
        """
        record = self._client.get_record(self._table, record_id)
        raw = record.get("fields", {}).get("Payload", "{}")
        return json.loads(raw)
