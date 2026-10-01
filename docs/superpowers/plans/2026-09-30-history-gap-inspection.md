# History Gap Inspection Implementation Plan

> **For agentic workers:** Use executing-plans to implement these tasks in this session. Track work with the checkboxes below.

**Goal:** Automatically discover and persist historical missing-message dates across enabled groups.

**Architecture:** A focused history_inspection service compares source IDs with archived IDs and saves per-group snapshots. A separate web integration module owns authenticated APIs and the periodic task. Collection templates expose settings, results and existing repair links.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, JavaScript, pytest.

### 1. Persistence and inspection

Files: `qq_digest/history_inspection.py`, `qq_digest/archive.py`, `tests/test_history_inspection.py`.

- [x] Write and run failing tests for missed middle messages, deduplication, timezone boundaries, disabled groups, unchanged messages/cursors, failed reads, recovery and persisted settings.
- [x] Add settings and group-state tables with safe defaults and cascading group deletion.
- [x] Implement strict `InspectionSettings(enabled=True, lookback_days=7, interval_hours=24)`, chunked ID comparisons, atomic successful group snapshots, failure preservation, run outcomes, paginated result loading and persisted interval checks.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_history_inspection.py -q` and confirm all cases pass.

### 2. APIs and periodic task

Files: `qq_digest/web/history_inspection.py`, `qq_digest/web/app.py`, `qq_digest/web/operations.py`, `tests/test_web.py`, `tests/test_operations.py`.

- [x] Add failing authentication, input validation, manual scan, conflict and scheduling tests.
- [x] Register GET `/api/history-inspection`, PUT `/api/history-inspection/settings`, POST `/api/history-inspection/run`; return persistent results plus actual running state.
- [x] Add `history_inspection` to mutual exclusion, start a loop after 30 seconds and poll every 60 seconds; cancel it with the existing lifespan tasks. Respect NTQQ enablement, settings and persistent interval.
- [x] Run focused service, web and operations tests.

### 3. Collection UI and release

Files: `qq_digest/web/templates/collect.html`, `qq_digest/desktop.py`, `docs/ROADMAP.md`.

- [x] Render automatic settings, optional source refresh, last run, errors, empty states, paginated group results and safely constructed single-day repair links. Parse both `start` and `end` deep-link parameters.
- [x] Check representative interactions at desktop and narrow browser widths, without touching production data. Store QA files under `D:\Cache\QQDigestDesktop\history-inspection-qa-2026-09-30`.
- [x] Run the complete test suite, diff checks and independent code review.
- [x] Build `D:\Apps\QQDigestDesktop-2026-10-01-P0-01a`, compare packaged templates, update `D:\Desktop\QQ Digest.lnk` and update roadmap. Finish by committing and pushing the verified implementation.

## Verification evidence

- Final suite: `D:\CodexTools\python\Scripts\python.exe -m pytest -q -o addopts=''` — 472 passed, one existing Starlette TestClient deprecation warning, 115.57 seconds.
- Independent review found a pagination edge case after enabled groups shrink. A failing regression test reproduced it; the server clamps the requested page and the client adopts the returned page. Reviewer confirmed the fix; no remaining important findings.
- Synthetic browser QA: settings persisted at 14 days / 48 hours, manual inspection reported partial failure honestly, pagination showed both result pages, and a repair link prefilled group 123 and identical start/end dates. Desktop 1280×900 and narrow 390×844 layouts checked; console warnings/errors empty.
- Screenshots and final test log: `D:\Cache\QQDigestDesktop\history-inspection-qa-2026-09-30\desktop.jpg`, `mobile.jpg`, `pytest-final.txt`.
- Actual EXE launch/login remains unverified: the previous automatic approval review rejected that action with “blocked by policy” and provided no further reason. This run does not retry the blocked action through another tool.
- PyInstaller build exited 0. Both new modules are included in the PYZ archive; packaged collect/dashboard templates match source SHA-256. Release executable exists (13,716,177 bytes), and `D:\Desktop\QQ Digest.lnk` was read back with the new release as its target.
