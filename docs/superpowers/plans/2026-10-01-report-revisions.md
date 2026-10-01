# Report Revisions Implementation Plan

> Use executing-plans to implement this plan in this session with independent review before merge.

**Goal:** Preserve report history and human corrections across successful regeneration.

**Architecture:** SQLite revisions and correction notes live in a focused service. Report record functions accept generated contents and save snapshots within the publication transaction. Authenticated routes expose history, notes and regeneration; report details show them without mixing historical and current evidence.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, JavaScript, pytest.

### 1. Version persistence

Files: `qq_digest/archive.py`, new `qq_digest/report_revisions.py`, `qq_digest/pipeline.py`, `qq_digest/manual_summary.py`, `qq_digest/cli.py`, new `tests/test_report_revisions.py`.

- [x] Add failing tests: two publications preserve v1/v2, rollback keeps one version and old files, old reports seed before replacement, unreadable old reports preserve partial legacy content and allow repair, daily/range IDs remain separate, deleting a group clears snapshots.
- [x] Add revisions/corrections tables with group and version cascade. Extend report record functions with optional `revision_markdown`, `revision_payload`, `revision_reason`; capture legacy content before upsert and append new content after upsert. Pass contents from all three publication paths.
- [x] Implement paginated versions, exact version reads and validated notes with expected-version conflict. Run focused report, pipeline, CLI and manual-summary tests.

### 2. Regeneration and API

Files: new `qq_digest/report_regeneration.py`, new `qq_digest/web/report_revisions.py`, `qq_digest/web/app.py`, tests.

- [x] Add failing authentication, invalid content, stale-version and operation-conflict tests.
- [x] Expose history/detail, note create/status and regenerate APIs. Worker holds the existing manual-summary claim through completion; saves snapshots and file pair atomically, closes AI/archive connections and preserves current report on failure.
- [x] Test regeneration using synthetic messages and fake AI: original window and group only, no QQ refresh or notifications, revision reason preserved, no accidental reuse, failure rollback.

### 3. UI and release

Files: `qq_digest/web/templates/reports.html`, `qq_digest/desktop.py`, `docs/ROADMAP.md`.

- [x] Add version selector, read-only historical notice, correction form/list and handled/reopen controls. Add regeneration with required reason and current expected version; keep request tokens scoped to selected report and historical evidence hidden.
- [x] Use synthetic browser QA at 1280 and 390 widths. QA/build files: `D:\Cache\QQDigestDesktop\report-revisions-qa-2026-10-01`.
- [x] Run full tests/diff checks and independent code review. Build `D:\Apps\QQDigestDesktop-2026-10-01-P1-04`, verify templates/modules and update `D:\Desktop\QQ Digest.lnk`; update roadmap, commit/push.

## Verification evidence

- Full suite: `D:\CodexTools\python\Scripts\python.exe -m pytest -q -o addopts=''`: **492 passed**, one pre-existing Starlette TestClient deprecation warning. Log: `D:\Cache\QQDigestDesktop\report-revisions-qa-2026-10-01\pytest-final.txt`.
- Independent review confirmed fixes for inclusive daily cutoffs, exclusive full-day legacy fallback on repeated regeneration, immutable payload recovery after file damage, body/version consistency, and historical view QA isolation. Each identified regression was reproduced before its fix; final focused review verification passed 20 cases with no remaining important issue.
- Browser QA used synthetic messages and fake AI on port 8770: version pagination and historical read-only view, correction create/resolve/reopen, persistence after server restart and regeneration, reason recording, and 390-pixel form layout. No warning/error console messages. Screenshots: `desktop.jpg` and `mobile.jpg` in the QA directory. Temporary viewport override and QA tab were cleared.
- Build exited successfully. Release: `D:\Apps\QQDigestDesktop-2026-10-01-P1-04\QQDigestDesktop.exe` (13,733,223 bytes). Packaged `reports.html` and `dashboard.html` SHA-256 matched source; the archive includes `qq_digest.report_revisions`, `qq_digest.report_regeneration` and `qq_digest.web.report_revisions`. `D:\Desktop\QQ Digest.lnk` target and working directory were updated and read back. Build log: `build-final.txt` in the QA directory. Production source/data were not used by synthetic QA; the build only carries the project's existing desktop configuration through its established release script.
- Actual EXE launch remains unverified because earlier automatic approval review rejected launching/logging into the EXE as “blocked by policy” without a more specific reason; that action was not retried through another tool.
