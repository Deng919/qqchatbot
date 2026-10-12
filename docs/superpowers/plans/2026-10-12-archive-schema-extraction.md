# Archive Schema Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Separate archive initialization from data access while preserving current database behavior.

**Architecture:** Move the existing SQL and upgrades into one connection-based function. Keep the `Archive._migrate` adapter, transaction ownership and message-index installation order. No schema redesign or new migration framework.

**Tech Stack:** Python, SQLite, pytest, existing Node CJS checks.

The user authorized this scope and requested autonomous execution without further routine approvals. Worktree: `C:/Users/xun/.codex/worktrees/runner-dedup/chatbot`. Baseline: `fc2391a`.

## Task 1: Add focused compatibility tests

**File:** Create `tests/test_archive_schema.py`.

- [x] Add an empty-database test using a real SQLite connection with `sqlite3.Row`, initialize through the new function and assert required columns and default rows:

```python
def test_initialize_empty_archive(tmp_path):
    from qq_digest.archive_schema import initialize_archive_schema
    with sqlite3.connect(tmp_path / 'archive.sqlite') as connection:
        connection.row_factory = sqlite3.Row
        initialize_archive_schema(connection)
        for table, required in {
            'groups': {'category', 'read_images', 'read_files'},
            'tasks': {'revision'},
            'reports': {'input_fingerprint', 'source_message_count'},
            'jobs': {'target_date', 'retry_of_job_id'},
            'send_log': {'manual_retry_count', 'recipient'},
        }.items():
            columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
            assert required <= columns
        assert connection.execute('SELECT revision FROM feature_settings').fetchone()[0] == 0
```

- [x] Add a representative legacy archive test with the original `groups` columns through `collection_window_days`, seed one disabled group with a Chinese name and saved keywords. Run initialization twice, assert saved values remain, new group flags default to zero and category defaults to general. Compare `sqlite_master` rows across the second run.

```python
def test_initialize_legacy_groups_preserves_saved_configuration(tmp_path):
    from qq_digest.archive_schema import initialize_archive_schema
    with sqlite3.connect(tmp_path / 'legacy.sqlite') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("""CREATE TABLE groups (
            group_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            enabled INTEGER NOT NULL, daily_summary INTEGER NOT NULL,
            template TEXT NOT NULL, keywords TEXT NOT NULL,
            important_candidates INTEGER NOT NULL,
            collection_window_days INTEGER NOT NULL)""")
        connection.execute('INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)',
                           (123, '旧群', 0, 1, 'concise', '["资料"]', 1, 7))
        initialize_archive_schema(connection)
        saved = dict(connection.execute('SELECT * FROM groups').fetchone())
        schema = [tuple(row) for row in connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        initialize_archive_schema(connection)
        assert dict(connection.execute('SELECT * FROM groups').fetchone()) == saved
        assert (saved['enabled'], saved['keywords'], saved['collection_window_days']) == (0, '["资料"]', 7)
        assert (saved['category'], saved['read_images'], saved['read_files']) == ('general', 0, 0)
        assert [tuple(row) for row in connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')] == schema
```

- [x] Add an `Archive.open` reopening integration test: ingest a real message, save group configuration and sync state, close/reopen twice; assert message rowid, count, sync cursor, group configuration, schema and foreign key activation remain. Use `tests.factories.make_message`, `GroupConfig` and `Archive`; retain existing FTS integration tests.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive_schema.py -o addopts='' -q`. Confirm the new-function tests fail because `archive_schema` does not exist. Existing integration behavior may already pass.

## Task 2: Extract the implementation

**Files:** Create `qq_digest/archive_schema.py`, modify `qq_digest/archive.py`.

- [x] Move the statements inside the existing migration transaction into the new function:

```python
import sqlite3


def initialize_archive_schema(connection: sqlite3.Connection) -> None:
    """Initialize tables and upgrade an archive using its configured connection.

    The caller supplies sqlite3.Row and owns transaction completion.
    """
```

Copy the baseline statements without editing SQL, defaults, order or exception handling. Replace `self.connection` references with the argument. When reindenting Python, leave multiline SQL string contents unchanged, including their whitespace. Do not introduce commit/rollback calls here.

- [x] Add `from .archive_schema import initialize_archive_schema` to `archive.py` and replace only `_migrate` with:

```python
    def _migrate(self) -> None:
        with self.transaction():
            initialize_archive_schema(self.connection)
```

- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive_schema.py tests/test_archive.py tests/test_task_inbox.py tests/test_message_search.py -o addopts='' -q`. Expect all pass.
- [x] Compare the baseline migration AST string constants in order with the extracted function's constants (excluding its documentation string); compare the remaining `Archive` methods to the baseline. Confirm exact preservation.
- [x] Commit production extraction and tests as `refactor: separate archive schema initialization`.

## Task 3: Review, verify and integrate

- [x] Request an independent read-only review of the baseline-to-HEAD changes; investigate actual issues, keep fixes within the approved scope.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest -o addopts='' -q` with default Windows encoding. Expect zero failures; report skips and warnings accurately.
- [x] Run every `tests/*.cjs` using Node and check each process exit code. Run `D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest`, and `git diff --check fc2391a`.
- [x] Record test results and completion in this plan. Do not mark unrelated Web scheduler or domain splits complete.
- [x] Verify original checkout has no tracked changes and its HEAD is still the baseline, then fast-forward it to the validated worktree HEAD. Preserve the existing untracked activity lock and legacy templates.
- [x] Compare merged tree identity against the validated worktree, run the focused integration tests in the original checkout, and report the actual paths and final commit. Do not package, publish or call real external services.

## Verification record

- Baseline focused suite: 57 passed. New tests before extraction: two expected missing-module failures, one existing-behavior pass.
- Focused suite after extraction: 60 passed in 25.88s.
- AST comparison: the entire migration statement body is identical after connection-argument substitution, including all 24 string constants. Every other Archive method is identical to the baseline.
- Independent read-only review of `fc2391a..6579986`: no actionable findings; merge readiness conditional on full verification.
- Node checks: all 29 CJS scripts exited successfully. `compileall` and cumulative `git diff --check fc2391a` passed.
- Full Python regression: 1,269 passed, 2 skipped, 1 warning in 426.13s. The skipped symlink tests were rerun with `-rs`: this Windows environment cannot create the required symlinks. The warning is an existing Starlette/httpx deprecation, unrelated to the extraction.
- Fast-forwarded original branch `codex/public-beta-2026-10-06` from `fc2391a` to implementation commit `6579986`. Both committed trees were `ecc719ab6dc531c1aa8cdf89cc148cfb8b47c02a` before this documentation-only completion update.
- Original-checkout focused regression: 60 passed in 22.92s. Compilation and cumulative diff checks also passed. No tracked local changes; SHA-256 values of the activity lock and two untracked legacy templates stayed identical.
- Original checkout: `D:/Desktop/AI/chatbot`. Managed worktree retained: `C:/Users/xun/.codex/worktrees/runner-dedup/chatbot`.
- Real QQ/AI/notification end-to-end checks and desktop packaging were outside this extraction's scope and were not performed.
