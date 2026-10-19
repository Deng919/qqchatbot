# Secondhand Quality and Behavior Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Correct secondhand extraction and display so bundles stay intact, repeated selling/long-term wants/suspected promotion are visible, and every optional field is source-verifiable.

**Architecture:** Keep raw messages and source snapshots unchanged. Extend `market_posts` with source-scoped bundle metadata; validate and persist canonical fields in `SecondhandService`; derive behavior metrics from stored snapshots at read time so reprocessing is idempotent and labels remain explainable. Rebuild existing rows from the local archive in a private copy before replacing the display table.

**Tech Stack:** Python, SQLite, FastAPI/Jinja, vanilla JavaScript, pytest, CJS UI tests, independent Chromium/Playwright.

**Spec:** docs/superpowers/specs/2026-10-19-secondhand-quality-design.md

## Global Constraints

- Use `D:\CodexTools\python\Scripts\python.exe` for Python work.
- Keep raw messages, credentials, local configs, caches, and unrelated untracked files out of Git.
- Keep evidence source-scoped; never infer seller identity, price, status, or bundle membership from nickname, adjacent messages, or unverified images.
- Use the existing archive transaction and operation lock; AI calls stay outside SQL transactions.
- Default date window remains local-time today and previous six calendar days.
- Do not automate or close Codex IAB; use an independent browser for UI verification.

## Review Focus

- A single source saying “打包/全套/合集” with shared evidence remains one visible bundle and exposes its components in detail.
- A source with separately priced items or explicit “单独出售” still produces separate items.
- AI paraphrases in description/condition and full-title price strings are rejected or normalized before persistence.
- Repeated seller activity across groups and days gets explainable counts; same-day multi-group activity is not mislabeled as long-term.
- “疑似推广” is cautious, source-backed, and never hides evidence or changes transaction direction.

### Task 1: Lock extraction and bundle behavior with failing tests

**Files:**
- Modify: `tests/test_secondhand.py`
- Modify: `tests/test_secondhand_runner.py`

**Interfaces:**
- Tests exercise `SecondhandService.accept_units`, `list_items`, and `detail` through existing fixtures.

- [x] **Step 1: Add a failing test for source-scoped bundle persistence.**
  Use one source with two components and `bundle_mode='bundle'`; assert one list item, `bundle_count == 2`, and detail exposes both component titles/evidence.
- [x] **Step 2: Add a failing test for separately priced items.**
  Use one source with two independently priced items; assert two visible items remain separate.
- [x] **Step 3: Add failing tests for exact optional-field evidence and canonical price text.**
  Assert an invented description or condition is rejected, and a price text containing the whole title is rejected while `800元` is accepted.
- [x] **Step 4: Add failing tests for behavior metrics.**
  Ingest the same seller/title across multiple groups/days and assert `repeat_count`, `active_days`, `span_days`, `重复发布`; ingest a want across three dates and assert `长期求购`; ingest a multi-service source and assert `疑似推广`.
- [x] **Step 5: Run the targeted tests and confirm RED for the missing behavior.**
  Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_secondhand.py tests/test_secondhand_runner.py -q`; failures must mention missing bundle/behavior fields or missing source validation, not fixture errors.

### Task 2: Persist bundles and derive behavior metrics

**Files:**
- Modify: `qq_digest/secondhand_schema.py`
- Modify: `qq_digest/secondhand.py`
- Modify: `qq_digest/secondhand_runner.py`
- Modify: `tests/test_secondhand.py`
- Modify: `tests/test_secondhand_runner.py`

**Interfaces:**
- `SecondhandService.accept_units(units, payload, validate_only=False)` accepts optional `bundle_mode`, `bundle_label`, and `components` fields and persists source-scoped metadata.
- `SecondhandService.list_items(...)` returns `repeat_count`, `active_days`, `span_days`, `behavior_flags`, `bundle_mode`, `bundle_count`, and `components`.
- `SecondhandService.detail(item_id)` returns the same item metadata and all source rows.

- [x] **Step 1: Add schema migration fields and a compatibility test.**
  Add four non-null columns with safe defaults and make initialization idempotent for old databases.
- [x] **Step 2: Implement minimal validation helpers.**
  Require optional description, condition, and bundle label to be continuous substrings of evidence; require short numeric price snippets; validate component evidence against the same source text.
- [x] **Step 3: Implement deterministic source-scoped bundle persistence.**
  Assign a stable bundle key for a source bundle, share the parent item key, store component JSON, and keep separately priced/explicitly separate items independent.
- [x] **Step 4: Implement read-time activity aggregation.**
  Calculate counts from source rows using local dates, then attach flags using the spec thresholds and cautious promotion keywords/component count.
- [x] **Step 5: Update the extraction prompt and payload compatibility.**
  Tell the AI to return one bundle with components when the source explicitly bundles items, while preserving existing payloads with default single mode.
- [x] **Step 6: Run targeted tests and then the full Python suite.**
  Targeted tests must pass first; then run `D:\CodexTools\python\Scripts\python.exe -m pytest -q` and fix regressions before moving on.

### Task 3: Make the UI explain the new grouping

**Files:**
- Modify: `qq_digest/web/templates/secondhand_script.html`
- Modify: `tests/secondhand_ui.cjs`
- Modify: `tests/test_secondhand_web.py`

**Interfaces:**
- API payload fields from Task 2 are rendered as safe text in list/detail views; no new endpoint is required.

- [x] **Step 1: Add failing CJS assertions for behavior and bundle labels.**
  Feed a bundle and a repeated item to the existing renderer and assert visible labels include component count and behavior flags.
- [x] **Step 2: Render compact badges and counts in the list.**
  Show bundle, repeat, long-wanted, and suspected-promotion labels beside the title; show source/group/active-day counts without altering existing filters.
- [x] **Step 3: Render components and behavior statistics in detail.**
  Use text nodes only; keep source evidence and image/source actions unchanged.
- [x] **Step 4: Run CJS and web tests.**
  Run `node tests/secondhand_ui.cjs` and relevant pytest files; then run the independent browser smoke flow without closing Codex windows.

### Task 4: Rebuild existing secondhand data and deliver

**Files:**
- Create: `D:\Cache\QQDigestSecondhand-20261019\quality-rebuild-summary.json`
- Modify: `docs/superpowers/specs/2026-10-19-secondhand-verification.md`
- Modify: `qq_digest/desktop.py` if the backend marker changes for the release

**Interfaces:**
- Use the existing archive and import runner; raw messages remain unchanged. QA output contains counts and validation results only.

- [x] **Step 1: Run the new extractor against a private copy of the existing archive.**
  Keep the old database as rollback; record source count, visible bundle count, behavior-label counts, unavailable/failed counts, and evidence validation counts.
- [x] **Step 2: Review representative bundle/repeat/long-wanted/promotion rows.**
  Confirm source evidence opens and separately priced messages remain separate.
- [x] **Step 3: Replace only the derived `market_posts` rows transactionally after QA passes.**
  Keep `messages`, attachments, scan receipts, blocks, and favorites intact; record before/after counts.
- [x] **Step 4: Run full pytest, all CJS tests, independent browser smoke, and package verification.**
  Do not claim completion until every command passes.
- [x] **Step 5: Commit the source/tests/docs and push the current work branch.**
  Use a focused commit message; verify remote SHA. Never stage local credentials, databases, caches, or the three pre-existing untracked files.

## Commit Cadence

- Commit after Task 2 when Python tests are green.
- Commit after Task 3 when UI tests and browser smoke are green.
- Final commit after Task 4 data rebuild and full verification.


## Execution record — 2026-10-19

- Task 1: complete. New bundle, source-validation, behavior tests were observed RED before the implementation. Separately priced baseline behavior remained green.
- Task 2: implementation complete. Added source-scoped metadata, deterministic bundle/collection recovery, seller/title activity metrics, optional exact-evidence constraints, and forced refresh support. Whole-suite verification remains a final gate.
- Task 3: complete. All 40 CJS scripts passed. Independent Chromium verified original search/favorite/block/image/forward flows at seven viewport sizes; quality-specific checks verified bundles, original components, repeat/wanted/promotion badges at five sizes. Packaged EXE passed the same quality-specific checks against synthetic data.
- Task 4: in progress. Final archive repair is deterministic rather than another AI pass. A 596-source AI retry completed only 416 sources and failed 180, so that snapshot was rejected. Existing 252 extracted sources were revalidated with zero failures, 336 rows became 323 rows and 256 visible listings, including 8 collections; original messages/scans stayed identical and the second repair was byte-for-byte idempotent at the row-value level.
- Ruling: use `collection` for unpriced same-post lists — the real archive has multi-item lists without an explicit requirement to buy everything together. Cost if wrong: presentation grouping can be expanded to inspect every original component.
- Ruling: statistics use seller QQ/direction/normalized title while physical-item identity remains conservative; historical queries exclude future activity — changed descriptions must not conceal repeated activity or falsely merge different goods. Cost if wrong: related posts may be marked repeated while remaining separate physical listings.
- Ruling: repair existing verified extraction instead of publishing partial AI re-extraction — strict source and price validation rejected 180 sources; copying that run would retain mixed old/new quality. Cost: the pre-existing 11 failed extractions and 7 unavailable forwards remain explicitly unresolved.
- Fresh final reviewer found five P2 issues: bundle ID stability, overbroad favorite transfer, explicit bundle bypass of independent pricing, course/notes promotion false positive, and local-quotation bundle splitting. Each was reproduced RED then fixed GREEN; idempotence was additionally checked against the real archive.
- Package verification: 117 runtime modules, 55 templates, release manifest validated; maintenance-only `secondhand_repair` is intentionally excluded from the desktop runtime because it is executed against a staging database by the developer.

- Task 4: complete. Final suite 1656 passed/2 skipped; native update succeeded, final snapshot published under the coordinator lock after message sync completed, real production UI verified with zero script errors. Source/messages/scan records preserved; file paths and exact counts recorded in the verification document.
- Final: fixed all five reviewer findings — regressions observed RED→GREEN, final whole suite green. No deferred review findings.
- Ruling: one combined commit instead of interim task commits — schema, runtime and templates were reviewed and validated as one coupled fix; cost if wrong is a larger but fully tested atomic rollback unit.
