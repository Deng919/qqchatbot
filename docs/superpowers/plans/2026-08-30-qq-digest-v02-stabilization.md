# QQ Digest v0.2 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing local QQ digest workflow reliable enough for daily unattended use while preserving the working NTQQ decoder and current archive data.

**Architecture:** Keep the collector and archive format, but make every workflow report explicit completeness and use one database connection per background operation. Align the AI JSON contract with the prompt, make reports replaceable when their input changes, and centralize destructive group cleanup. Keep FastAPI and vanilla HTML/CSS, splitting behavior through small service helpers before any larger module extraction.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Pydantic 2, Typer, Jinja2, vanilla JavaScript/CSS, pytest.

---

### Task 1: Repair CLI command registration

**Files:**
- Modify: `qq_digest/cli.py`
- Test: `tests/test_cli.py`

- [ ] Add a CLI test that invokes `run-daily --help` and asserts that its description is the daily pipeline rather than database refresh.
- [ ] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py -q` and confirm the test fails.
- [ ] Move `@app.command("run-daily")` immediately above `run_daily()` and leave `refresh()` with only its own decorator.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Align the AI contract and source references

**Files:**
- Modify: `qq_digest/summary.py`
- Modify: `qq_digest/prompt_builder.py`
- Modify: `qq_digest/reports.py`
- Test: `tests/test_summary.py`
- Test: `tests/test_reports.py`

- [ ] Add tests proving that prompt-compliant `url` resources and structured tasks validate successfully.
- [ ] Add a test proving each context line contains the source `msg_id` and a truncation flag is returned.
- [ ] Run the focused tests and confirm they fail on the current schema/context implementation.
- [ ] Define `Resource.url`, a structured `TaskOutput`, and strict candidate source ID validation.
- [ ] Return a context object containing text, character count, and `truncated`; include source IDs in formatted messages.
- [ ] Render task owner, description, and optional deadline consistently in Markdown.
- [ ] Re-run focused tests.

### Task 3: Prevent partial collection from advancing sync state

**Files:**
- Modify: `qq_digest/collector/base.py`
- Modify: `qq_digest/collector/ntqq.py`
- Modify: `qq_digest/pipeline.py`
- Modify: `qq_digest/refresh.py`
- Test: `tests/test_ntqq_collector.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_refresh.py`

- [ ] Add tests that corrupted metadata/blob reads raise an explicit collection error instead of returning partial success.
- [ ] Add a pipeline test proving failed collection does not advance `last_timestamp`.
- [ ] Add refresh validation tests requiring the expected core tables, not merely any table.
- [ ] Run focused tests and confirm expected failures.
- [ ] Introduce explicit collector compatibility/completeness exceptions and stop swallowing database errors.
- [ ] Only mark sync after a complete collection and successful ingest.
- [ ] Treat refresh as successful only when required databases validate; preserve prior valid files on failure.
- [ ] Re-run focused tests.

### Task 4: Make daily reports safely refreshable

**Files:**
- Modify: `qq_digest/archive.py`
- Modify: `qq_digest/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] Add a test where a second same-day run receives a new message and must replace the report.
- [ ] Add an input fingerprint and source message count to report metadata through a migration.
- [ ] Skip AI only when the current input fingerprint matches the stored report.
- [ ] Replace pending candidates created by the superseded report without touching confirmed knowledge items.
- [ ] Verify same-input runs remain idempotent and changed-input runs regenerate.

### Task 5: Centralize complete group deletion

**Files:**
- Modify: `qq_digest/archive.py`
- Modify: `qq_digest/web/app.py`
- Modify: `qq_digest/web/templates/groups.html`
- Test: `tests/test_archive.py`
- Test: `tests/test_web.py`

- [ ] Add tests proving `delete_group(group_id, purge=True)` removes messages, sync state, reports, candidates, send logs, and unconfirmed knowledge metadata in one transaction.
- [ ] Make the API return deletion counts and 404 for an unknown group.
- [ ] Update the confirmation text to state that related data is deleted.
- [ ] Purge test group IDs `100001`, `100002`, and `100003` only after automated behavior tests pass.

### Task 6: Isolate background database work and avoid blocking the Web server

**Files:**
- Modify: `qq_digest/web/app.py`
- Modify: `qq_digest/cli.py`
- Test: `tests/test_web.py`

- [ ] Add tests proving manual long-running endpoints reject overlapping jobs and expose a running state.
- [ ] Open a fresh Archive connection inside each worker thread.
- [ ] Run refresh, collect, and daily work with `asyncio.to_thread`.
- [ ] Persist scheduler success by reading the jobs table rather than only in-memory state.
- [ ] Close worker AI clients and database connections deterministically.

### Task 7: Upgrade the operational UI

**Files:**
- Modify: `qq_digest/web/templates/base.html`
- Modify: `qq_digest/web/templates/dashboard.html`
- Modify: `qq_digest/web/templates/groups.html`
- Modify: `qq_digest/web/templates/collect.html`
- Modify: `qq_digest/web/templates/reports.html`
- Test: `tests/test_web.py`

- [ ] Add HTML/API regression tests for escaped external values and new dashboard state fields.
- [ ] Replace generic square navigation markers with accessible text/icon treatment already available in the project.
- [ ] Rework the dashboard around today status, last refresh, pending review, active groups, and recent failures.
- [ ] Make group management searchable and responsive, with Chinese category/template labels and clear destructive actions.
- [ ] Paginate or cap raw message rendering so a month of messages cannot freeze the browser.
- [ ] Verify desktop and mobile views in a real browser, including console errors and primary interactions.

### Task 8: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `config/config.example.yaml`
- Test: all tests

- [ ] Document one authoritative scheduler, actual NTQQ support, local/mobile binding, backup location, and recovery behavior.
- [ ] Add `cryptography` as a declared dependency because `refresh.py` imports it directly.
- [ ] Run `D:\CodexTools\python\Scripts\python.exe -m pytest`.
- [ ] Run `D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest`.
- [ ] Run `git diff --check` and inspect the final diff for unrelated changes.
- [ ] Execute CLI help smoke tests and authenticated Web API/browser smoke tests.
