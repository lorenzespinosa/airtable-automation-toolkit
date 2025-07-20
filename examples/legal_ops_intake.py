"""
Example: Legal intake staging via Airtable buffer.

Flow: Lawmatics webhook → Airtable staging → Filevine project creation

This demonstrates the pattern where intake form submissions land in Airtable
first (visible, queryable, fixable) before being pushed to Filevine.
"""

import os
from dotenv import load_dotenv

from src.airtable_client import AirtableClient
from src.staging_sync import StagingSync
from src.audit_logger import AuditLogger

load_dotenv()

BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appXXXXXXXXXX")
API_KEY = os.getenv("AIRTABLE_API_KEY", "patXXXXXXXXXX")


def simulate_lawmatics_webhook():
    """Simulate an incoming lead from Lawmatics."""
    return [
        {
            "external_id": "LM-10042",
            "name": "Maria Garcia",
            "email": "maria.garcia@example.com",
            "phone": "+14075559876",
            "case_type": "Personal Injury",
            "source": "Google Ads",
            "intake_date": "2025-07-15T10:30:00Z",
            "notes": "Rear-end collision on I-4. SSN: 123-45-6789",
        },
        {
            "external_id": "LM-10043",
            "name": "James Wilson",
            "email": "jwilson@example.com",
            "phone": "+14075551234",
            "case_type": "Criminal Defense",
            "source": "Referral",
            "intake_date": "2025-07-15T11:00:00Z",
            "notes": "DUI charge, first offense.",
        },
    ]


def push_to_filevine(records):
    """Simulate pushing records to Filevine.

    In production, this would call the Filevine API to create projects.
    """
    print(f"[Filevine] Would create {len(records)} projects:")
    for r in records:
        print(f"  - {r['external_id']}: {r['data'].get('name', 'Unknown')}")
    # Return the records as-is to simulate success
    return records


def main():
    # Initialize components
    sync = StagingSync(base_id=BASE_ID, api_key=API_KEY, table_name="IntakeStaging")
    audit = AuditLogger(base_id=BASE_ID, api_key=API_KEY)

    # Run the sync
    print("=== Legal Intake Staging Demo ===\n")

    # Step 1: Stage incoming leads
    print("1. Staging leads from Lawmatics...")
    leads = simulate_lawmatics_webhook()
    staged = sync.push_to_staging(leads, source="lawmatics")
    print(f"   Staged {len(staged)} records\n")

    # Step 2: Log the intake (PII gets masked automatically)
    print("2. Writing audit log (PII masked)...")
    for lead in leads:
        audit.log(
            action="lead_staged",
            actor="lawmatics-webhook",
            details={
                "external_id": lead["external_id"],
                "name": lead["name"],
                "email": lead["email"],
                "phone": lead["phone"],
                "notes": lead["notes"],
            },
        )
    print("   Audit entries written\n")

    # Step 3: Pull pending and push to Filevine
    print("3. Syncing to Filevine...")
    pending = sync.pull_from_staging(status="pending")
    if pending:
        push_to_filevine(pending)
        for p in pending:
            sync.mark_synced(p["record_id"])
    print(f"   Synced {len(pending)} records\n")

    print("Done.")


if __name__ == "__main__":
    main()
