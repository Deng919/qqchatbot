# Archive schema extraction

Date: 2026-10-12. Baseline: `fc2391a`.

## Problem and approved scope

`qq_digest/archive.py` has 1,252 lines. Its `_migrate` method occupies about
386 lines of table creation, indexes and compatibility upgrades. Reading data
access code currently requires navigating past that separate responsibility.
The user approved extracting only schema initialization and migrations.

This is a responsibility split, not a reduction in total SQL or supported
functionality. Notifications, groups, messages, reports, candidates, jobs and
group deletion stay in `Archive` because they share its connection and transaction
contract. Existing features and saved data remain supported.

## Structure

- Add `qq_digest/archive_schema.py` with one function,
  `initialize_archive_schema(connection: sqlite3.Connection) -> None`.
- Move the SQL and compatibility logic from `_migrate` into that function.
  It uses the connection already configured by `Archive` with `sqlite3.Row`.
- Keep `Archive._migrate` as a small adapter that calls this function inside
  the existing `Archive.transaction()` context.
- Keep connection creation, foreign key activation and full-text index
  installation in `Archive`.

Do not add repository classes, mixins, a migration registry, version counters,
configuration switches or per-domain schema modules. A single ordinary function
is sufficient for the approved boundary.

## Compatibility and failure behavior

Preserve all SQL strings, parameters, statement order, column defaults, indexes
and existing `sqlite3.OperationalError` handling. Preserve the sync-state
backfill and message ingest high-water initialization.

Preserve initialization order: configure rows, enable foreign keys, run schema
initialization through `_migrate`, then install the message search index.
`Archive.open`, the constructor and every data access method retain their
signatures. Reopening an existing archive must retain data and identities.

Transaction ownership remains in `Archive`; the extracted function adds no
commit, rollback or new context manager. Existing `executescript` behavior is
retained. This refactor does not claim to make the entire migration atomic or
correct historical migration behavior beyond its current contract.

No new database columns or schema changes, URL changes, template changes,
live data migration, installer changes or external service calls are in scope.

## Verification

Use the existing archive, task-schema and message-search tests as the baseline.
Add focused tests for initializing an empty database, upgrading representative
old schemas and preserving saved rows after repeated initialization/reopening.
Check table/index/trigger definitions and foreign key activation across reopen.
Retain existing full-text failure/fallback tests and group deletion tests.

Compare extracted SQL constants and their order against the baseline source.
Run focused tests, the full Python suite, all existing CJS scripts,
`compileall` and `git diff --check` before claiming completion.

## Delivery

Work in the existing managed worktree at
`C:/Users/xun/.codex/worktrees/runner-dedup/chatbot` from the merged first-stage
baseline. Preserve the original workspace's untracked activity lock and legacy
templates. Review and integrate only after validation; desktop packaging and
real QQ/AI/notification end-to-end validation are separate work.
