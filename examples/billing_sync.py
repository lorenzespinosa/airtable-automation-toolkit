"""
Example: Billing sync between Filevine and Clio via Airtable buffer.

Flow: Filevine time entries → Airtable staging → Clio billing

Demonstrates bidirectional sync with conflict detection. Time entries
from Filevine are staged in Airtable, reviewed, then pushed to Clio
for invoicing.
"""

import os
from dotenv import load_dotenv

from src.staging_sync import StagingSync
from src.audit_logger import AuditLogger

load_dotenv()

BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appXXXXXXXXXX")
API_KEY = os.getenv("AIRTABLE_API_KEY", "patXXXXXXXXXX")


def fetch_filevine_time_entries():
    """Simulate fetching unbilled time entries from Filevine."""
    return [
        {
            "external_id": "FV-TE-8801",
            "attorney": "Alex Rivera",
            "matter": "Garcia v. Smith Insurance",
            "hours": 2.5,
            "rate": 350.00,
            "description": "Drafted demand letter and reviewed medical records",
            "date": "2025-09-10",
        },
        {
            "external_id": "FV-TE-8802",
            "attorney": "Sarah Chen",
            "matter": "Wilson DUI Defense",
            "hours": 1.0,
            "rate": 300.00,
            "description": "Court appearance - arraignment",
            "date": "2025-09-10",
        },
        {
            "external_id": "FV-TE-8803",
            "attorney": "Alex Rivera",
            "matter": "Thompson Slip & Fall",
            "hours": 0.5,
            "rate": 350.00,
            "description": "Client phone call re: settlement offer",
            "date": "2025-09-11",
        },
    ]


def push_to_clio(records):
    """Simulate pushing time entries to Clio for billing.

    In production, this would call Clio's API to create time entries.
    """
    print(f"  [Clio] Creating {len(records)} time entries:")
    total_billable = 0
    for r in records:
        data = r["data"]
        amount = data.get("hours", 0) * data.get("rate", 0)
        total_billable += amount
        print(
            f"    - {data.get('attorney')}: {data.get('hours')}h @ ${data.get('rate')}/hr "
            f"= ${amount:.2f} ({data.get('matter')})"
        )
    print(f"  [Clio] Total billable: ${total_billable:.2f}")
    return records


def main():
    sync = StagingSync(base_id=BASE_ID, api_key=API_KEY, table_name="BillingStaging")
    audit = AuditLogger(base_id=BASE_ID, api_key=API_KEY)

    print("=== Billing Sync Demo (Filevine → Airtable → Clio) ===\n")

    # Full sync cycle
    print("Running full sync cycle...")
    summary = sync.sync(
        source_callable=fetch_filevine_time_entries,
        dest_callable=push_to_clio,
        source_name="filevine",
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
