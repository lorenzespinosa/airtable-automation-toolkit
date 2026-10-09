"""
Example: General intake staging via Airtable buffer.

Flow: Source API webhook → Airtable staging → Destination API project creation

This demonstrates the pattern where intake form submissions land in Airtable
first (visible, queryable, fixable) before being pushed to Destination API.
"""

import os
from dotenv import load_dotenv

from src.airtable_client import AirtableClient
from src.staging_sync import StagingSync
from src.audit_logger import AuditLogger

load_dotenv()

BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appXXXXXXXXXX")
API_KEY = os.getenv("AIRTABLE_API_KEY", "patXXXXXXXXXX")


def simulate_source_api_webhook():
    """Simulate an incoming lead from Source API."""
    return [
        {
            "external_id": "SRC-10042",
            "name": "Example Person A",
            "email": "person-a@example.com",
            "phone": "+14075559876",
            "request_type": "Service Request",
            "source": "Google Ads",
            "intake_date": "2025-07-15T10:30:00Z",
            "notes": "Fictional onboarding note; no client details.",
        },
        {
            "external_id": "SRC-10043",
            "name": "Example Person B",
            "email": "person-b@example.com",
            "phone": "+14075551234",
            "request_type": "General Inquiry",
            "source": "Referral",
            "intake_date": "2025-07-15T11:00:00Z",
            "notes": "Question about onboarding.",
        },
    ]


def push_to_destination_api(records):
    """Simulate pushing records to Destination API.

    In an implementation, replace this stub with a configured destination client.
    """
    print(f"[Destination API] Would create {len(records)} projects:")
    for r in records:
        print(f"  - {r['external_id']}: {r['data'].get('name', 'Unknown')}")
    # Return the records as-is to simulate success
    return records


def main():
    # Initialize components
    sync = StagingSync(base_id=BASE_ID, api_key=API_KEY, table_name="IntakeStaging")
    audit = AuditLogger(base_id=BASE_ID, api_key=API_KEY)

    # Run the sync
    print("=== General Intake Staging Demo ===\n")

    # Step 1: Stage incoming leads
    print("1. Staging leads from Source API...")
    leads = simulate_source_api_webhook()
    staged = sync.push_to_staging(leads, source="source_api")
    print(f"   Staged {len(staged)} records\n")

    # Step 2: Log example details (selected patterns are masked; verify output)
    print("2. Writing example audit log...")
    for lead in leads:
        audit.log(
            action="lead_staged",
            actor="source_api-webhook",
            details={
                "external_id": lead["external_id"],
                "name": lead["name"],
                "email": lead["email"],
                "phone": lead["phone"],
                "notes": lead["notes"],
            },
        )
    print("   Audit entries written\n")

    # Step 3: Pull pending and push to Destination API
    print("3. Syncing to Destination API...")
    pending = sync.pull_from_staging(status="pending")
    if pending:
        push_to_destination_api(pending)
        for p in pending:
            sync.mark_synced(p["record_id"])
    print(f"   Synced {len(pending)} records\n")

    print("Done.")


if __name__ == "__main__":
    main()
