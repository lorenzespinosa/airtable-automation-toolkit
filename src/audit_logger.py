"""
Audit logger that writes to Airtable with automatic PII masking.

All log entries are masked before writing — emails, phone numbers, and SSNs
are redacted so the audit table is safe to share with non-technical staff.

Expected Airtable table columns:
    - Action (Single line text)
    - Actor (Single line text)
    - Details (Long text — JSON string)
    - Timestamp (Date/time)
    - Level (Single select: INFO, WARNING, ERROR)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.airtable_client import AirtableClient, mask_pii

logger = logging.getLogger(__name__)


class AuditLogger:
    """Audit logger backed by an Airtable table.

    PII (emails, phone numbers, SSNs) in the ``details`` field is
    automatically masked before the record is written.

    Args:
        base_id: Airtable base ID.
        api_key: Airtable personal access token.
        table_name: Name of the audit log table. Default ``AuditLog``.
        mask_enabled: Whether to mask PII. Default ``True``.
    """

    LEVEL_INFO = "INFO"
    LEVEL_WARNING = "WARNING"
    LEVEL_ERROR = "ERROR"

    def __init__(
        self,
        base_id: str,
        api_key: str,
        table_name: str = "AuditLog",
        mask_enabled: bool = True,
    ) -> None:
        self._client = AirtableClient(base_id=base_id, api_key=api_key)
        self._table = table_name
        self._mask_enabled = mask_enabled

    def log(
        self,
        action: str,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
        level: str = LEVEL_INFO,
    ) -> Dict[str, Any]:
        """Write an audit log entry.

        Args:
            action: What happened (e.g. ``"lead_created"``, ``"record_deleted"``).
            actor: Who or what performed the action (e.g. ``"n8n-workflow-42"``).
            details: Optional dict of additional context. PII is auto-masked.
            level: Log level — INFO, WARNING, or ERROR.

        Returns:
            The created Airtable record.
        """
        masked_details = self.mask_pii(details) if details else {}

        fields: Dict[str, Any] = {
            "Action": action,
            "Actor": actor,
            "Details": json.dumps(masked_details, default=str),
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "Level": level,
        }

        record = self._client.create_record(self._table, fields)
        logger.info(
            "Audit log: action=%s actor=%s level=%s record_id=%s",
            action,
            actor,
            level,
            record["id"],
        )
        return record

    def log_error(
        self,
        action: str,
        actor: str,
        error: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Write an error-level audit log entry.

        Args:
            action: What failed.
            actor: Who or what triggered the action.
            error: Error message.
            details: Optional additional context.

        Returns:
            The created Airtable record.
        """
        combined_details = dict(details) if details else {}
        combined_details["error"] = error
        return self.log(action, actor, details=combined_details, level=self.LEVEL_ERROR)

    def mask_pii(self, data: Any) -> Any:
        """Mask PII in a data structure.

        Masks emails, phone numbers, and SSNs. Returns a new object —
        does not mutate the original.

        Args:
            data: String, dict, or list to mask.

        Returns:
            Masked copy of the data.
        """
        if not self._mask_enabled:
            return data
        return mask_pii(data)

    def query_logs(
        self,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query audit log records.

        Args:
            action: Filter by action name.
            actor: Filter by actor.
            level: Filter by level (INFO, WARNING, ERROR).
            limit: Max records to return.

        Returns:
            List of matching audit log records.
        """
        conditions: List[str] = []
        if action:
            conditions.append(f"{{Action}}='{action}'")
        if actor:
            conditions.append(f"{{Actor}}='{actor}'")
        if level:
            conditions.append(f"{{Level}}='{level}'")

        formula = None
        if len(conditions) == 1:
            formula = conditions[0]
        elif len(conditions) > 1:
            formula = "AND(" + ", ".join(conditions) + ")"

        return self._client.list_records(
            self._table,
            filter_formula=formula,
            max_records=limit,
            sort=[{"field": "Timestamp", "direction": "desc"}],
        )
