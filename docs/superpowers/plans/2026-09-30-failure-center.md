# Failure Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show persisted failures in one place and make supported retries traceable.

**Architecture:** `FailureCenterService` reads bounded rows from the existing archive; retry endpoints invoke the existing daily, sync and notification workflows. Small schema additions retain links and retry counts. A dedicated template renders the list without interpreting error text as HTML.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, vanilla JavaScript, pytest.

---

### Task 1: Persist retry lineage

**Files:** `qq_digest/archive.py`, `qq_digest/pipeline.py`, `qq_digest/sync.py`, `tests/test_archive.py`.

- [x] Add a failing archive test that opens a legacy database and checks `retry_of_job_id` and `manual_retry_count` exist after migration.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive.py -q`; confirm that test fails.
- [x] Add both columns and optional `retry_of_job_id` to `start_job`; pass it through daily and sync workflows. Add atomic `retry_failed_notification` with a conditional `WHERE status='failed'` update.
- [x] Run the focused archive, pipeline and sync tests.

### Task 2: Read a bounded failure timeline

**Files:** `qq_digest/failure_center.py`, `tests/test_failure_center.py`.

- [x] Add failing tests for parsed group failures, current sync errors, notification statuses, and recovery by later matching success.
- [x] Run the focused tests and confirm the intended failures.
- [x] Implement a bounded list service with stable IDs, timestamps, next-step labels and retry links. Malformed legacy JSON remains a readable job error.
- [x] Run the focused tests.

### Task 3: Wire authenticated retries

**Files:** `qq_digest/web/app.py`, `tests/test_web.py`.

- [x] Add failing API tests for authentication, invalid or stale retry IDs, and successful lineage/notification requeue.
- [x] Run the focused tests and confirm failures.
- [x] Add `GET /api/failures`, `POST /api/failures/jobs/{id}/retry`, and `POST /api/failures/notifications/{id}/retry`, reusing existing operation coordination.
- [x] Run focused API tests.

### Task 4: Surface and package the page

**Files:** `qq_digest/web/templates/failures.html`, `qq_digest/web/templates/base.html`, `qq_digest/web/templates/dashboard.html`, `scripts/build_desktop.py`, `docs/ROADMAP.md`.

- [x] Add a failing page/authentication test.
- [x] Run it and confirm failure.
- [x] Add the page, navigation and dashboard link; list active and recovered entries with safe text and clear buttons. Add the template to the desktop build list.
- [x] Run the page test, then full pytest and real-browser UI checks. Update roadmap only after verification.

### Task 5: Release

- [x] Build into a new dated folder under `D:\Apps`, with build files under `D:\Cache\QQDigestDesktop`.
- [x] Verify the packaged template and EXE, update the desktop shortcut when the release can be launched.
- [x] Inspect `git diff`, stage only intended files, commit and push to `main` after verification.
