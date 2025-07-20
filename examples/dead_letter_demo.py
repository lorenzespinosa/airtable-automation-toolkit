"""
Example: Dead letter queue usage.

Demonstrates capturing failed webhook payloads and API errors,
then reviewing and retrying them.
"""

import os
from dotenv import load_dotenv

from src.dead_letter_queue import DeadLetterQueue

load_dotenv()

BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appXXXXXXXXXX")
API_KEY = os.getenv("AIRTABLE_API_KEY", "patXXXXXXXXXX")


def main():
    dlq = DeadLetterQueue(base_id=BASE_ID, api_key=API_KEY, table_name="DLQ")

    print("=== Dead Letter Queue Demo ===\n")

    # Simulate failed operations
    print("1. Enqueueing failed operations...")

    dlq.enqueue(
        source="n8n-intake-webhook",
        error="ConnectionError: Filevine API returned 503 Service Unavailable",
        payload={
            "lead_id": 12345,
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+14075551234",
        },
    )
    print("   Enqueued: Filevine API timeout")

    dlq.enqueue(
        source="clio-billing-sync",
        error="ValidationError: Missing required field 'matter_id'",
        payload={
            "time_entry_id": "TE-9901",
            "hours": 2.5,
            "description": "Client consultation",
        },
    )
    print("   Enqueued: Clio validation error")

    dlq.enqueue(
        source="openphone-sms-webhook",
        error="RateLimitError: OpenPhone API rate limit exceeded (429)",
        payload={
            "from": "+14075559999",
            "to": "+14075550001",
            "message": "Appointment confirmed for Monday at 2pm",
        },
    )
    print("   Enqueued: OpenPhone rate limit\n")

    # List pending items
    print("2. Listing pending items...")
    pending = dlq.list_pending()
    for item in pending:
        fields = item.get("fields", {})
        print(f"   [{fields.get('Source')}] {fields.get('Error')[:60]}...")
    print(f"   Total pending: {len(pending)}\n")

    # Dequeue and process
    print("3. Dequeuing oldest item for processing...")
    item = dlq.dequeue()
    if item:
        fields = item.get("fields", {})
        print(f"   Processing: {fields.get('Source')}")
        print(f"   Error: {fields.get('Error')}")

        # Simulate retry logic
        payload = dlq.get_payload(item["id"])
        print(f"   Payload: {payload}")

        # Mark for retry
        print("   Marking for retry...")
        dlq.retry(item["id"])
        print("   Retry count incremented\n")

    # Resolve an item
    print("4. Resolving an item after manual fix...")
    if pending and len(pending) > 1:
        resolve_id = pending[1]["id"]
        dlq.resolve(resolve_id)
        print(f"   Resolved: {pending[1].get('fields', {}).get('Source')}\n")

    # Final status
    print("5. Final queue status:")
    all_items = dlq.list_all()
    for item in all_items:
        fields = item.get("fields", {})
        print(
            f"   [{fields.get('Status'):12s}] {fields.get('Source')} — "
            f"retries: {fields.get('RetryCount', 0)}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
