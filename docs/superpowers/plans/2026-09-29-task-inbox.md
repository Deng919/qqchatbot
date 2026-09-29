# 任务与待办收件箱 Implementation Plan

> **For agentic workers:** Use test-driven-development for each task. This plan is executed inline because the current checkout contains the previous roadmap work.

**Goal:** Deliver sourced task suggestions, confirmed tasks, message creation, and in-app due reminders.

**Architecture:** `TaskInboxService` reads validated daily-report evidence and writes confirmed tasks to SQLite. FastAPI exposes authenticated endpoints. A focused template renders suggestions and tracked tasks; search links to message creation.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, browser JavaScript, PyInstaller.

---

### Task 1: Persistence and service

**Files:** `qq_digest/archive.py`, `qq_digest/task_inbox.py`, `tests/test_task_inbox.py`.

- [x] Write tests for cited suggestions, invalid citations, decision deduplication, direct message creation, immutable sources, ISO dates and due classification.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_task_inbox.py -q` and confirm missing behavior fails.
- [x] Add the two SQLite tables and implement the smallest service API: `suggestions`, `decide`, `create_from_message`, `list_tasks`, `update_task`.
- [x] Run the focused tests until green.

### Task 2: Authenticated HTTP flow

**Files:** `qq_digest/web/app.py`, `tests/test_web.py`.

- [x] Write failing endpoint tests for auth, suggestion confirmation, direct message creation, updates and validation errors.
- [x] Add request models and routes for listing suggestions/tasks, decisions, creation and updates.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q`.

### Task 3: Interface and packaging

**Files:** `qq_digest/web/templates/tasks.html`, `qq_digest/web/templates/base.html`, `qq_digest/web/templates/search.html`, `qq_digest/web/templates/dashboard.html`, `scripts/build_desktop.py`, `qq_digest/desktop.py`.

- [x] Add a responsive task inbox with evidence context, edits, state changes and due markers; add search-message entry and navigation.
- [x] Verify browser interactions and narrow viewport; keep user text in `textContent`.
- [x] Include the template in the desktop package and change the backend marker.

### Task 4: Documentation and release

**Files:** `docs/ROADMAP.md`, `README.md`.

- [x] Record exact delivered behavior and source limits; run full `D:\CodexTools\python\Scripts\python.exe -m pytest -q` and `git diff --check`.
- [x] Build a new release under `D:\Apps`, verify EXE and template, and update the desktop shortcut without modifying an occupied release.
