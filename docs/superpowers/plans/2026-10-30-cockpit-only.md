# Cockpit Only and Archive Reprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** One Cockpit connection throughout the project and all archived messages reprocessed.
**Architecture:** Existing AIClient and publication/revision services; one configuration and a resumable local operations script.
**Tech Stack:** Python, Pydantic, FastAPI, SQLite, Jinja, pytest, independent Playwright.
**Spec:** docs/superpowers/plans/2026-10-30-cockpit-only-spec.md

## Global Constraints

- Python D:\CodexTools\python\Scripts\python.exe; repository D:\Desktop\AI\chatbot.
- Never print credentials; no local configs, DBs, reports or chat content in Git.
- Preserve unrelated untracked files and all prior report versions.
- No new history collection; respect existing block controls and report excluded coverage explicitly.
- Shared machine tools stay installed. No IAB automation.

## Review Focus

- Legacy YAML cannot trigger bridge or DeepSeek fallback.
- Every AI purpose uses the same endpoint/model/key and usage accounting.
- Settings and first-use updates preserve protected keys and revision conflict behavior.
- Reprocessing cannot reuse an old fingerprint in lieu of regenerating.
- Failures and unavailable attachments cannot count as completed AI processing.

### Task 1: One Cockpit integration

**Files:** qq_digest/config.py, ai/factory.py, ai/bridge.py, ai/__init__.py, attachments.py, distribution.py, first_use.py, web/app.py, web/ai_connection_routes.py, web/archive_qa_routes.py, web/first_use_routes.py, related templates, config example, packaging/docs and affected tests.
**Interfaces:** build_ai_client(config, purpose=...) continues returning the generic AIClient. Config.ai exposes base_url/model/key/json_mode/timeout/context/retries; remove provider/profile routing. Existing settings and first-use routes expose one connection.

- [x] Add tests proving old priority fields cannot route to bridge, all purposes share config, settings preserve secrets/conflicts and UI has no old provider choices. Run and capture expected failures.
- [x] Implement the single connection, remove obsolete integrations/docs/tests and migrate supported legacy input without silently invoking it.
- [x] Run focused tests then full pytest; report known baseline failure separately. Self-review, commit only task source/tests/docs.
- [x] Independent task review, fix concrete findings and verify covering tests.

### Task 2: Backed-up archive processing

**Files:** local scripts and ledger under D:\Cache\QQDigestCockpitVerification\2026-10-30 (not Git); protected production config/secrets only locally.
**Interfaces:** existing ManualSummaryService/GroupSummaryBuilder, Archive publication/revision methods and secondhand_runner.refresh.

- [ ] Inventory group-day/message coverage, controls and active operations; back up SQLite consistently, reports and config.
- [ ] Configure Cockpit endpoint http://127.0.0.1:62406/v1 and model gpt-6.1-sol with protected local key; verify API and independent browser flows.
- [ ] Create resumable ledger with fixed coverage. Force summary regeneration through normal revision publication; marketplace force refresh then retry incomplete sources. Process until every task has explicit outcome.
- [ ] Verify report message counts, current revisions, marketplace scans and failures against snapshot. Commit/push verified code and verify remote SHA. Report actual exclusions/limitations and ledger path.
