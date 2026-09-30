# Report Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain what each report and daily run covers, with visible limitations and honest unknown states.

**Architecture:** A focused `report_completeness` service reads persisted report diagnostics and daily job outcomes. The shared summary builder writes input warnings to new Markdown/JSON reports. Existing list, detail and health APIs expose coverage without rewriting old reports; templates render concise and detailed notices.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, vanilla JavaScript, pytest.

---

### Task 1: Report input coverage

**Files:** `qq_digest/report_completeness.py`, `qq_digest/group_summary.py`, `qq_digest/pipeline.py`, `tests/test_group_summary.py`, `tests/test_report_completeness.py`.

- [x] Write failing tests for a low sample, truncated context, serious archive mismatch and missing diagnostics.
- [x] Run the focused tests to confirm the failures.
- [x] Add a pure coverage calculation, write warnings into new report JSON/Markdown, and pass the collection mismatch flag from the daily pipeline.
- [x] Run focused tests and confirm that normal reports do not gain a false warning.

### Task 2: Daily cross-group coverage

**Files:** `qq_digest/report_completeness.py`, `tests/test_report_completeness.py`.

- [x] Write failing tests for partial success, a group with no report, full per-group coverage, and legacy jobs without outcomes.
- [x] Run the tests to confirm failure.
- [x] Query the latest job by target date and its persisted per-group outcomes, then classify coverage and produce concise reasons.
- [x] Run the focused tests.

### Task 3: APIs and pages

**Files:** `qq_digest/web/app.py`, `qq_digest/web/templates/reports.html`, `qq_digest/web/templates/dashboard.html`, `tests/test_web.py`.

- [x] Add failing API tests for report list/detail and health coverage, including legacy and damaged report JSON.
- [x] Run the tests to confirm failure.
- [x] Return input and day coverage from existing authenticated APIs. Add list badges, a detail notice and a prominent total-coverage message in the dashboard.
- [x] Run focused tests and inspect desktop and narrow browser layouts with representative data.

### Task 4: Verify and release

**Files:** `qq_digest/desktop.py`, `docs/ROADMAP.md`.

- [x] Run full pytest and inspect the staged diff.
- [x] Build a new dated EXE under `D:\Apps` and verify the packaged templates.
- [ ] Start the EXE briefly: automated launch/login check was rejected by automatic approval review (`blocked by policy`, no detailed reason). Build succeeded; runtime smoke remains unverified.
- [x] Update the desktop shortcut, mark the roadmap item complete, commit and push the intended files.

## Verification evidence

- Full suite: 448 passed, 1 existing Starlette TestClient deprecation warning.
- Browser: dashboard and report list/detail at 1280 × 900 and 390 × 844; no warning/error console entries. Fixed compressed list badges by stacking details below the list on viewports up to 1400 px.
- Independent code review: no important actionable findings.
- Release: `D:\Apps\QQDigestDesktop-2026-09-30-P0-05\QQDigestDesktop.exe`; build cache and simulated QA data under `D:\Cache\QQDigestDesktop`.
- Existing reports retain their AI input fingerprint when collection mismatch is absent; their diagnostic statistics are interpreted at read time without triggering another AI request. A changed collection mismatch flag invalidates reuse so its warning cannot be silently lost.
