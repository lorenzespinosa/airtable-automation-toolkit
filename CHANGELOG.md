# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- `pyproject.toml` with project metadata and a `dev` extra — the package is now `pip install -e ".[dev]"` installable
- GitHub Actions CI running the test suite on Python 3.11 and 3.12
- Unit tests for `StagingSync` (checksum upsert, conflict detection, full sync cycle) and `batch_ops` — total coverage now 87%

### Fixed
- README: removed a non-existent "enable caching" performance tip and expanded the API reference to match the actual public methods

## [0.3.0] - 2025-12-15

### Added
- `StagingSync.detect_conflicts()` for bidirectional conflict detection
- `StagingSync.sync()` method for full source-to-destination sync cycles
- `AuditLogger.log_error()` convenience method
- `AuditLogger.query_logs()` with multi-field filtering
- Batch operations: `create_records()`, `update_records()`, `delete_records()`
- Checksum-based idempotent upserts in staging sync

### Changed
- Improved PII masking to handle nested data structures
- Rate limit interval now configurable via constructor

### Fixed
- Pagination offset handling when `max_records` is set
- SSN masking regex to avoid false positives on date strings

## [0.2.0] - 2025-09-20

### Added
- `DeadLetterQueue` class for failed operation tracking
- `AuditLogger` with automatic PII masking (emails, phones, SSNs)
- `DeadLetterQueue.get_payload()` for JSON payload retrieval
- `DeadLetterQueue.resolve()` to mark items as resolved
- Example scripts for legal intake, billing sync, and DLQ usage
- Unit tests with mocked API calls

### Changed
- `AirtableClient` now uses `requests.Session` for connection reuse
- Retry logic handles 502/503/504 in addition to 500

## [0.1.0] - 2025-07-01

### Added
- Initial release
- `AirtableClient` with CRUD operations
- Automatic pagination on `list_records()`
- Exponential backoff retry (3 attempts) on transient errors
- Rate limiting (5 req/sec) to stay within Airtable limits
- PII masking utility (`mask_pii`)
- Structured exceptions: `AirtableError`, `AirtableRateLimitError`, `AirtableNotFoundError`, `AirtableValidationError`
- Type hints and docstrings throughout
- `.env.example` for configuration
- MIT license
