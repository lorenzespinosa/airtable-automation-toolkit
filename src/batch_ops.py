# Batch Operations
"""Batch create/update operations for Airtable with chunking."""
from .airtable_client import AirtableClient

def batch_create(client: AirtableClient, table: str, records: list, chunk_size: int = 10):
    """Create records in batches of chunk_size."""
    results = []
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i+chunk_size]
        for record in chunk:
            results.append(client.create_record(table, record))
    return results
