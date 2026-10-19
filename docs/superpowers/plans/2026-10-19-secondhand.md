# Secondhand Implementation Plan

> **For agentic workers:** Use executing-plans for inline implementation; one fresh whole-branch review before delivery.

**Goal:** Ship the approved marketplace reader and import all8 local secondhand groups; default seven days.
**Architecture:** Archive-owned membership, offers, source snapshots, scan receipts and blocked accounts; strict evidence extraction; reuse collectors, attachment reader and configured AI. Dedicated authenticated routes and Jinja list/detail page.
**Tech Stack:** Python/FastAPI/SQLite/Jinja/vanilla JS, pytest/CJS, independent Playwright.
**Spec:** docs/superpowers/specs/2026-10-19-secondhand-design.md

## Global Constraints
- Use D:\CodexTools\python\Scripts\python.exe; reuse attached clean worktree on codex/secondhand-20261019.
- Keep production config, chats, database and credentials out of Git; QA in D:\Cache\QQDigestSecondhand-20261019.
- Use existing operations coordinator, explicit group membership and evidence; no nickname identity inference or guessed prices/status.
- Preserve original archive and existing group settings; new secondhand groups do not join ordinary daily summaries.

## Review Focus
- Cross-group duplicate and two identical-looking goods from different sellers must stay distinguishable.
- A late forwarded old price/status cannot replace a newer direct source; unknown original author cannot become the forwarder.
- Blocking must filter before image/AI work and remove visible old entries without deleting source archive; failed saves leave UI usable.
- Seven-day boundaries use local timezone and original source date, with unavailable-image/forward coverage shown.
- Restore/migrate/delete must include new tables; worker calls outside SQL transactions and stale UI callbacks guarded.

### Task1: Evidence extraction and persistence
Files: new secondhand_schema.py, secondhand_evidence.py, secondhand.py; archive_schema.py. Tests test_secondhand.py.
Interfaces: SecondhandService(archive,timezone_name).add_groups(groups), list_items(...), detail(item_id), set_favorite(item_id,bool), block(qq), unblock(qq), blocked(), accept_units(units,AI_result), pending_units(messages), import_local(config,start,end).
- [x] Write failing tests for7day bounds, quoted price validation,8group discovery, duplicate source idempotence, different sellers, price chronology, explicit sold, unknown forward author, blocks and foreign-key cleanup.
- [x] Implement strict structured extraction with bounded source units, evidence checks and conservative merge; transactions only for persistence. Store scan receipts and coverage.
- [x] Verify related tests then commit.

### Task2: Import/update workflow and page
Files: new secondhand_runner.py, web/secondhand_routes.py, templates/secondhand.html and secondhand_script.html; feature/nav/package integrations. Tests test_secondhand_web.py and secondhand_ui.cjs.
Interfaces: runner.refresh(config,archive,date_from,date_to,collect=True,on_progress=None), get/list/detail/blocks, POSTrefresh, PATCHfavorite, POST/DELETEblock. Default dates today-6days..today. Page loads groups/blocked/items, update explicit and asynchronous via a bounded existing coordinator claim.
- [x] Add failing auth/validation/defaultwindow/featureoff tests and CJS stale/duplicate/failure scenarios.
- [x] Implement imports, fingerprint cache and configured AI batches; metadata-only unavailable forwards explicit. Add membership controls with discovery action and existing group links.
- [x] Implement list/detail, all approved filters, source dialogs/images, favorites and blocks. Preserve nullable seller identity; exact source IDs only.
- [x] Verify Python/CJS and browser geometry/workflows then commit.

### Task3: Real import, full review and delivery
- [x] Inspect real source completeness; add8groups and collect recentweek under coordinator lock. Run real extraction using configured AI; save aggregate results without chats in QA logs.
- [x] Run whole suite, all CJS, fresh final review and independent synthetic browser/EXE verification; fix demonstrated issues.
- [x] Package new immutable D:\Apps release, verify modules/templates, merge working checkout, rerun related checks, update recoverable desktop entry and push GitHub branches with remote SHA verification.
- [x] Update roadmap and verification record with actual imported/processed/unavailable counts and remaining external-data limits.
