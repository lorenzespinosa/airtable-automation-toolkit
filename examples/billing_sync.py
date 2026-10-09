"""
Example: Billing sync between Source API and Destination API via Airtable buffer.

Flow: Source API time entries → Airtable staging → Destination API billing

Demonstrates bidirectional sync with conflict detection. Time entries
from Source API are staged in Airtable, reviewed, then pushed to Destination API
for invoicing.
"""

import os
from dotenv import load_dotenv

from src.staging_sync import StagingSync
from src.audit_logger import AuditLogger

load_dotenv()

BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appXXXXXXXXXX")
API_KEY = os.getenv("AIRTABLE_API_KEY", "patXXXXXXXXXX")


def fetch_source_api_time_entries():
    """Simulate fetching unbilled time entries from Source API."""
    return [
        {
            "external_id": "SRC-TE-8801",
            "worker": "Worker A",
            "project": "Project Alpha",
            "hours": 2.5,
            "rate": 350.00,
            "description": "Prepared a project update and reviewed source records",
            "date": "2025-09-10",
        },
        {
            "external_id": "SRC-TE-8802",
            "worker": "Worker B",
            "project": "Project Beta",
            "hours": 1.0,
            "rate": 300.00,
            "description": "Scheduled stakeholder review",
            "date": "2025-09-10",
        },
        {
            "external_id": "SRC-TE-8803",
            "worker": "Worker A",
            "project": "Project Gamma",
            "hours": 0.5,
            "rate": 350.00,
            "description": "Project status call",
            "date": "2025-09-11",
        },
    ]


def push_to_destination_api(records):
    """Simulate pushing time entries to Destination API for billing.

    In an implementation, replace this stub with a configured destination client.
    """
    print(f"  [Destination API] Creating {len(records)} time entries:")
    total_billable = 0
    for r in records:
        data = r["data"]
        amount = data.get("hours", 0) * data.get("rate", 0)
        total_billable += amount
        print(
            f"    - {data.get('worker')}: {data.get('hours')}h @ ${data.get('rate')}/hr "
            f"= ${amount:.2f} ({data.get('project')})"
        )
    print(f"  [Destination API] Total billable: ${total_billable:.2f}")
    return records


def main():
    sync = StagingSync(base_id=BASE_ID, api_key=API_KEY, table_name="BillingStaging")
    audit = AuditLogger(base_id=BASE_ID, api_key=API_KEY)

    print("=== Billing Sync Demo (Source API → Airtable → Destination API) ===\n")

    # Full sync cycle
    print("Running full sync cycle...")
    summary = sync.sync(
        source_callable=fetch_source_api_time_entries,
        dest_callable=push_to_destination_api,
        source_name="source_api",
    )

    print(f"\nSync summary:")
    print(f"  Source records: {summary['source_records']}")
    print(f"  Staged:         {summary['staged']}")
    print(f"  Conflicts:      {summary['conflicts']}")
    print(f"  Synced:         {summary['synced']}")
    print(f"  Errors:         {summary['errors']}")

    # Log the sync
    audit.log(
        action="billing_sync_completed",
        actor="billing-sync-cron",
        details=summary,
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
