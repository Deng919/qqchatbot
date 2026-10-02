# Feature Switches Implementation Plan

Use executing-plans in this session; independent review before merge.

**Goal:** Let users reduce visible features and pause optional scheduled work without deleting data.
**Architecture:** One archive-backed feature service, one HTTP integration module, request-scoped template flags and live scheduling guards.
**Tech Stack:** SQLite, Python, FastAPI, Jinja2, JavaScript, pytest.

1. [x] Add failing service/API tests for persistence, strict validation, stale writes, auth, disabled routes and re-enabling without data loss.
2. [x] Implement `qq_digest/features.py`, archive migration, `qq_digest/web/features.py`, API/middleware/template context and daily/sync/inspection guards.
3. [x] Add settings switches and presets; hide optional nav, dashboard links, search task links and report/collect panels. Keep QA usable without revisions.
4. [x] Focused/full tests, independent review and 1280/390 browser QA with synthetic data.
5. [x] Save user's chosen simple preset, build `D:\Apps\QQDigestDesktop-2026-10-02-Features`, verify packaged templates/modules, update `D:\Desktop\QQ Digest.lnk`, document and push.

QA artifacts: `D:\Cache\QQDigestDesktop\feature-switches-qa-2026-10-02`. Preserve unrelated `reports (1).html`.

## Verification

- Full suite: **501 passed**, one existing Starlette TestClient deprecation warning, 83.15 seconds. `pytest-final.txt` contains the result; command used `D:\CodexTools\python\Scripts\python.exe -m pytest -q -o addopts=''`.
- Independent review reproduced report-ID spelling bypass and knowledge search links remaining after review was disabled. Added failing regressions before fixes; final reviewer checked 24 alternate URL combinations returning 403 and actual JavaScript knowledge rendering without review links. No remaining important issue.
- Synthetic browser QA at port 8771: simple/all presets, independent QA on with revisions off, automatic daily remaining off when preset changes, persistence across restart, seven optional toggles and simple navigation. 1280/390 screenshots are `desktop.jpg`, `mobile.jpg`; no warning/error console logs. QA server stopped, successful tab closed and viewport override reset.
- User-authorized simple preset saved in `D:\Desktop\AI\chatbot\archive\archive.sqlite`; message, daily/range report, candidate, knowledge and task counts unchanged. Automatic task choices preserved. Added M-01–M-04 gradual modularization goals to `docs/ROADMAP.md` as requested.
- Release build succeeded, six changed template SHA-256 values matched source and both feature modules were present in the PyInstaller archive. Desktop shortcut points to `D:\Apps\QQDigestDesktop-2026-10-02-Features\QQDigestDesktop.exe`. Build log: `build-final.txt`.
- Actual EXE launch was not retried: earlier automatic approval review rejected launching/logging into the EXE as “blocked by policy” without a specific reason. Source browser verification and package verification do not establish actual EXE runtime success.
