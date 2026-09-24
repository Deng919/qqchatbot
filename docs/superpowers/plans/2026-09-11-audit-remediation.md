# Audit Remediation Implementation Plan

> Execute inline with TDD and an independent knowledge subtask; preserve the user's current master checkout and do not stage or commit.

**Goal:** Fix the 12 reproducible audit findings and connect the supplied OpenCode API credential.

**Architecture:** Keep report identity stable and publish candidate/reference/file updates transactionally. Preserve shared candidates by checking both report tables before garbage collection. Use configuration seeding rather than overwriting user-edited group settings. Bind reuse to complete summary inputs and valid report files. Load API credentials from a private file and construct fallback clients on demand.

**Tech Stack:** Python, SQLite, FastAPI, pytest, existing PowerShell service entry point.

## Tasks and verification

- [x] Promote the 12 `work/audit-20260911/test_review_findings.py` reproductions into independent formal regressions in `tests/test_audit_regressions.py`; replace the startup simulation with an actual serve-path test. Run first to confirm failures.
- [x] AI: add `AIConfig.api_key_file`, environment-first credential resolution, and lazy provider construction in `qq_digest/ai/factory.py`; test missing fallback configuration without invoking primary network, file loading and environment precedence. Configure the private file path and official OpenCode endpoint; execute a synthetic summary using only test messages.
- [x] Candidate lifecycle: add non-committing report recording and unreferenced pending-candidate cleanup in `qq_digest/archive.py`; update daily and manual publication to create/reuse candidates, record new references, clean only unreferenced old pending rows, install files, then commit/finalize. Test same-ID reuse, cross-report references, rollback and reviewed-candidate preservation.
- [x] Knowledge: in `qq_digest/knowledge.py`, match complete marker lines and persist content; propagate content from both Web and Bot confirm paths. Verify ID 10 then ID 1 and full experience body persistence.
- [x] Group state: seed configured groups only when missing via `Archive.seed_groups`, track configuration bootstrap IDs so deleted configured groups do not reappear, use this across CLI bootstrap entry points; protect group deletion with the existing operation coordinator. Test restart after edits and deletions and active-operation HTTP 409.
- [x] Cache: add a shared input fingerprint including message fields, group settings, resolved template, timezone, knowledge, context budget and system prompt content. Cache reuse requires both readable Markdown and JSON-object output. Test setting changes and missing/corrupt output regeneration.
- [x] Summary input: include local dates in message lines, skip a single over-budget row while continuing to collect earlier valid rows, reject an empty post-cleaning/context input, and filter candidate message IDs against included source IDs. Test oversize/empty input, cross-day context, unknown references and mixed-validity references.
- [x] Verify full test suite and compile; review implementation against each audit finding. Check current runtime jobs, restart the exact service only when idle, validate authenticated report endpoints and real synthetic AI output. Retain user's real reports and knowledge data.

Commands:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_audit_regressions.py -o addopts='' -q
D:\CodexTools\python\Scripts\python.exe -m pytest -o addopts='' -q
D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest tests
git diff --check
```

The original audit remains a historical record. Write verification results into `work/audit-20260911/remediation-results.md` after executing the checks.
