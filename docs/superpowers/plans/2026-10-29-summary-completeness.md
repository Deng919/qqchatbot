# Summary Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make historical summaries traceable and future oversized summaries complete without silent input loss.
**Architecture:** Existing summarizer performs bounded chronological partial summaries and bounded pairwise merges only for oversized input. History repair is a backed-up, reviewed data operation with original timestamp preservation.
**Tech Stack:** Python, Pydantic, SQLite, existing report services and pytest.
**Spec:** docs/superpowers/specs/2026-10-29-summary-completeness-design.md

## Global Constraints
No guessed citation, external fact insertion, private-data Git commit, unrelated configuration change, or silent truncation. Preserve failure safety and budgets, original report windows and generation times.

## Review Focus
Oversized single source, per-chunk fabricated IDs, merge losing earlier findings, merge failures and runaway calls, historical version/file concurrency.

### Task 1: Future generation
- [x] Add failing tests to tests/test_summary_chunking.py for complete ordered source delivery, single oversized messages, bounded merge, references and fail-safe errors.
- [x] Implement in qq_digest/summary.py and a focused helper if justified; bump group_summary format fingerprint and backend ID.
- [x] Run summary/group/manual generation regression tests, independent code review.

### Task 2: Historical repair
- [x] Freeze archive/reports in D:/Cache/QQDigestSummaryCompleteness-20261029 and inventory missing citations/truncated reports.
- [x] Review every affected claim and full source windows for previously truncated reports; make per-field patches and record unresolved gaps.
- [x] Verify rollback and timestamp preservation on snapshot before transactionally applying data changes.

### Task 3: Delivery
- [x] Run appropriate full regressions, package and activate a new D:/Apps release, verify live source drilldown.
- [x] Commit only source/tests/necessary nonprivate docs, push work branch and verify remote SHA; report actual coverage and remaining gaps.

## Execution ledger

- 2026-10-29: Reviewed and revised 49 of 293 daily reports; 244 unchanged. Seven historically truncated windows contained 8,193 archived messages. Original timestamps, generation diagnostics and earlier revisions were preserved. Private snapshots and per-claim evidence remain outside Git under the QA cache above.
- Ruling: Keep the original generation diagnostics even after a full-window editorial review. Record the later review separately; citation-only repair cannot claim full-window coverage.
- 2026-10-30: Fresh data verification passed: zero uncited nonempty body claims, unchanged candidates/read states/other checked tables, SQLite integrity `ok`.
- 2026-10-30: Resumed targeted regressions passed (84 summary/manual/revision tests, then 100 tests covering prior full-suite failures). Updated old AI fixtures to cite actual synthetic source IDs. The empty-context regression now uses filtered system noise, since long valid messages are processed in chunks.
- 2026-10-30: Independent final review found no important issues; 95 chunking, coverage and report-write regressions passed. Oversized stage output rejection is accounted as failed; interrupted file rollback can be retried without deleting an already restored file.
- 2026-10-30: Built `D:/Apps/QQDigestDesktop-2026-10-30-SummaryCompleteness-Release`; all 119 bundled Python modules and 56 templates matched source. Isolated packaged WebView plus independent Chromium desktop/mobile checks passed: citations open original messages, source panel closes, full-report coverage reflects the later review, no page errors or horizontal overflow.
- 2026-10-30: Full repository regression completed: 1,899 passed, zero failed/errors, three skipped because Windows symbolic-link creation was unavailable without additional OS permission. No test failures were suppressed.
- 2026-10-30: Native updater switched successfully to the new release; the desktop shortcut targets its executable, configuration hash is unchanged, and the previous Motion release is retained as rollback. The live backend read back all 49 revised reports and the seven full-window labels successfully. Integration uses the existing `codex/public-beta-2026-10-06` work branch; the delivery message records the commit and verified remote SHA.
- Validation limit: provider calls in tests are simulated. Sending every cleaned source and preserving cited IDs does not independently prove that a real model retains every semantic detail or verify external claims in chat messages.
