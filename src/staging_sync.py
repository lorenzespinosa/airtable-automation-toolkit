"""
Bidirectional sync between Airtable staging tables and external APIs.

Uses Airtable as a visible buffer — records land in staging, get validated,
then push to destination systems. Supports conflict detection and idempotent
upserts via an ``external_id`` field.

Expected Airtable table columns:
    - external_id (Single line text — unique identifier from source system)
    - data (Long text — JSON string of the full record)
    - sync_status (Single select: pending, synced, conflict, error)
    - last_synced (Date/time)
    - source (Single line text — origin system name)
    - checksum (Single line text — hash for conflict detection)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.airtable_client import AirtableClient

logger = logging.getLogger(__name__)


def _compute_checksum(data: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of a dict for conflict detection."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class StagingSync:
    """Bidirectional sync engine using Airtable as a staging buffer.

    Records are identified by ``external_id``. Push operations are
    idempotent — if a record with the same external_id exists, it gets
    updated instead of duplicated.

    Args:
        base_id: Airtable base ID.
        api_key: Airtable personal access token.
        table_name: Staging table name. Default ``Staging``.
    """

    STATUS_PENDING = "pending"
    STATUS_SYNCED = "synced"
    STATUS_CONFLICT = "conflict"
    STATUS_ERROR = "error"

    def __init__(
        self,
        base_id: str,
        api_key: str,
        table_name: str = "Staging",
    ) -> None:
        self._client = AirtableClient(base_id=base_id, api_key=api_key)
        self._table = table_name

    def push_to_staging(
        self,
        records: Sequence[Dict[str, Any]],
        source: str = "api",
    ) -> List[Dict[str, Any]]:
        """Push records to the staging table (idempotent upsert).

        Each record must contain an ``external_id`` key. If a record with
        that external_id already exists in staging, it will be updated.
        Otherwise a new record is created.

        Args:
            records: List of dicts. Each must have an ``external_id`` key.
            source: Name of the source system.

        Returns:
            List of created/updated Airtable records.
        """
        if not records:
            return []

        results: List[Dict[str, Any]] = []

        for record in records:
            external_id = record.get("external_id")
            if not external_id:
                raise ValueError(f"Record missing required 'external_id' key: {record}")

            data_without_id = {k: v for k, v in record.items() if k != "external_id"}
            checksum = _compute_checksum(data_without_id)
            now = datetime.now(timezone.utc).isoformat()

            fields = {
                "external_id": str(external_id),
                "data": json.dumps(data_without_id, default=str),
                "sync_status": self.STATUS_PENDING,
                "last_synced": now,
                "source": source,
                "checksum": checksum,
            }

            # Check if record already exists
            existing = self._client.list_records(
                self._table,
                filter_formula=f"{{external_id}}='{external_id}'",
                max_records=1,
            )

            if existing:
                existing_record = existing[0]
                existing_checksum = existing_record.get("fields", {}).get("checksum", "")

                if existing_checksum == checksum:
                    # Data unchanged — skip update
                    logger.debug("Skipping unchanged record: external_id=%s", external_id)
                    results.append(existing_record)
                    continue

                # Data changed — update
                updated = self._client.update_record(
                    self._table, existing_record["id"], fields
                )
                logger.info("Updated staging record: external_id=%s", external_id)
                results.append(updated)
            else:
                # New record
                created = self._client.create_record(self._table, fields)
                logger.info("Created staging record: external_id=%s", external_id)
                results.append(created)

        return results

    def pull_from_staging(
        self,
        filter_formula: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Pull records from the staging table.

        Returns parsed records with their data field deserialized.

        Args:
            filter_formula: Optional Airtable formula filter.
            status: Filter by sync_status (pending, synced, conflict, error).
            limit: Max records to return.

        Returns:
            List of dicts with ``record_id``, ``external_id``, ``data``,
            ``sync_status``, and ``source`` keys.
        """
        conditions: List[str] = []
        if filter_formula:
            conditions.append(filter_formula)
        if status:
            conditions.append(f"{{sync_status}}='{status}'")

        formula = None
        if len(conditions) == 1:
            formula = conditions[0]
        elif len(conditions) > 1:
            formula = "AND(" + ", ".join(conditions) + ")"

        raw_records = self._client.list_records(
            self._table,
            filter_formula=formula,
            max_records=limit,
            sort=[{"field": "last_synced", "direction": "desc"}],
        )

        parsed: List[Dict[str, Any]] = []
        for r in raw_records:
            fields = r.get("fields", {})
            data_str = fields.get("data", "{}")
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = {"_raw": data_str}

            parsed.append(
                {
                    "record_id": r["id"],
                    "external_id": fields.get("external_id"),
                    "data": data,
                    "sync_status": fields.get("sync_status"),
                    "source": fields.get("source"),
                    "checksum": fields.get("checksum"),
                }
            )

        return parsed

    def mark_synced(self, record_id: str) -> Dict[str, Any]:
        """Mark a staging record as successfully synced.

        Args:
            record_id: Airtable record ID.

        Returns:
            The updated record.
        """
        return self._client.update_record(
            self._table,
            record_id,
            {
                "sync_status": self.STATUS_SYNCED,
                "last_synced": datetime.now(timezone.utc).isoformat(),
            },
        )

    def mark_error(self, record_id: str, error: str) -> Dict[str, Any]:
        """Mark a staging record as failed.

        Args:
            record_id: Airtable record ID.
            error: Error description.

        Returns:
            The updated record.
        """
        return self._client.update_record(
            self._table,
            record_id,
            {"sync_status": self.STATUS_ERROR},
        )

    def detect_conflicts(
        self,
        records: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Detect records that have been modified in both source and staging.

        Compares checksums to find conflicts.

        Args:
            records: Source records (each must have ``external_id``).

        Returns:
            List of conflict dicts with ``external_id``, ``source_checksum``,
            and ``staging_checksum``.
        """
        conflicts: List[Dict[str, Any]] = []

        for record in records:
            external_id = record.get("external_id")
            if not external_id:
                continue

            data_without_id = {k: v for k, v in record.items() if k != "external_id"}
            source_checksum = _compute_checksum(data_without_id)

            existing = self._client.list_records(
                self._table,
                filter_formula=f"{{external_id}}='{external_id}'",
                max_records=1,
            )

            if existing:
                staging_checksum = existing[0].get("fields", {}).get("checksum", "")
                if staging_checksum and staging_checksum != source_checksum:
                    conflicts.append(
                        {
                            "external_id": external_id,
                            "source_checksum": source_checksum,
                            "staging_checksum": staging_checksum,
                            "staging_record_id": existing[0]["id"],
                        }
                    )
                    # Mark as conflict in Airtable
                    self._client.update_record(
                        self._table,
                        existing[0]["id"],
                        {"sync_status": self.STATUS_CONFLICT},
                    )

        return conflicts

    def sync(
        self,
        source_callable: Callable[[], List[Dict[str, Any]]],
        dest_callable: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
        source_name: str = "source",
    ) -> Dict[str, Any]:
        """Bidirectional sync: pull from source, stage, push to destination.

        1. Calls ``source_callable()`` to get source records.
        2. Pushes them to staging (idempotent upsert).
        3. Detects conflicts.
        4. Pulls conflict-free pending records from staging.
        5. Calls ``dest_callable(records)`` to push to destination.
        6. Marks successful records as synced.

        Args:
            source_callable: Zero-arg function returning source records.
            dest_callable: Function that accepts a list of records and pushes
                           them to the destination. Returns successfully synced records.
            source_name: Name for the source system.

        Returns:
            Summary dict with counts of staged, conflicts, synced, and errors.
        """
        # Step 1: Fetch from source
        source_records = source_callable()
        logger.info("Sync: fetched %d records from source", len(source_records))

        # Step 2: Push to staging
        staged = self.push_to_staging(source_records, source=source_name)
        logger.info("Sync: staged %d records", len(staged))

        # Step 3: Detect conflicts
        conflicts = self.detect_conflicts(source_records)
        logger.info("Sync: detected %d conflicts", len(conflicts))

        # Step 4: Pull pending (non-conflict) records
        pending = self.pull_from_staging(status=self.STATUS_PENDING)
        logger.info("Sync: %d pending records to sync", len(pending))

        # Step 5: Push to destination
        synced_count = 0
        error_count = 0

        if pending:
            try:
                synced_records = dest_callable(pending)
                synced_ids = {r.get("record_id") or r.get("external_id") for r in synced_records}

                for p in pending:
                    if p["record_id"] in synced_ids or p["external_id"] in synced_ids:
                        self.mark_synced(p["record_id"])
                        synced_count += 1
                    else:
                        self.mark_error(p["record_id"], "Not in destination response")
                        error_count += 1

            except Exception as exc:
                logger.error("Sync: destination push failed: %s", exc)
                for p in pending:
                    self.mark_error(p["record_id"], str(exc))
                    error_count += 1

        summary = {
            "source_records": len(source_records),
            "staged": len(staged),
            "conflicts": len(conflicts),
            "synced": synced_count,
            "errors": error_count,
        }
        logger.info("Sync complete: %s", summary)
        return summary
