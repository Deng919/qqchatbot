# QQ Digest Reliability and Review Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete stable NTQQ snapshots, full candidate review, configurable knowledge files, and automatic AI/notification retries without rebuilding existing archives.

**Architecture:** Extend the current FastAPI and SQLite application with small service boundaries. Keep schema changes backward compatible, use atomic snapshot/decryption validation, and drive every behavior with focused pytest tests before implementation.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Pydantic 2, Typer, Jinja2, vanilla JavaScript, pytest.

---

### Task 1: Stable encrypted snapshots

**Files:** `qq_digest/refresh.py`, `tests/test_refresh.py`

- [ ] Add failing tests for source mutation, snapshot cleanup, core `quick_check`, and preserved output on failure.
- [ ] Add a snapshot helper that copies with before/after stat validation and bounded retries.
- [ ] Decrypt snapshots only and validate required tables plus integrity before atomic replacement.
- [ ] Run focused refresh tests and a real refresh.

### Task 2: Candidate query and review lifecycle

**Files:** `qq_digest/candidates.py`, `qq_digest/web/app.py`, `qq_digest/web/templates/candidates.html`, `tests/test_web.py`

- [ ] Add failing API tests for status/group/type/date/text filters and candidate details.
- [ ] Add query methods and authenticated JSON endpoints.
- [ ] Replace the pending-only page with state tabs, filters, detail view, and restore action.
- [ ] Run focused Web tests and desktop/mobile browser checks.

### Task 3: Configurable knowledge targets

**Files:** `qq_digest/config.py`, `qq_digest/knowledge.py`, `qq_digest/cli.py`, `qq_digest/web/app.py`, `qq_digest/notify/commands.py`, `config/config.example.yaml`, `tests/test_config.py`, `tests/test_knowledge.py`

- [ ] Add failing tests for default, relative, and absolute knowledge paths.
- [ ] Add `KnowledgeConfig` and resolved target mapping.
- [ ] Make all entry points construct `KnowledgeWriter` from the resolved mapping.
- [ ] Record Bot confirmations in `knowledge_items` using the same idempotent path as Web confirmation.

### Task 4: AI and daily retry policy

**Files:** `qq_digest/config.py`, `qq_digest/ai/client.py`, `qq_digest/web/app.py`, `tests/test_ai_client.py`, `tests/test_web.py`

- [ ] Add failing tests for retryable statuses, `Retry-After`, permanent 4xx, and daily retry limits.
- [ ] Implement bounded AI retry without retrying response-validation failures.
- [ ] Persist and reconstruct same-day daily attempts from jobs.
- [ ] Make the scheduler retry failures after the configured interval.

### Task 5: Notification queue

**Files:** `qq_digest/archive.py`, `qq_digest/notify/queue.py`, `qq_digest/pipeline.py`, `qq_digest/web/app.py`, `tests/test_archive.py`, `tests/test_notify.py`

- [ ] Add failing migration and queue lifecycle tests.
- [ ] Add backward-compatible `send_log` queue columns and archive operations.
- [ ] Enqueue notifications after report creation and process due entries with bounded backoff.
- [ ] Start and stop a notification worker with the Web application lifecycle.

### Task 6: Verification

- [ ] Run focused tests after every task.
- [ ] Run full pytest, compileall, and `git diff --check`.
- [ ] Refresh real databases and confirm core `quick_check=ok`.
- [ ] Verify candidate filtering/details and responsive layout in a real browser.
