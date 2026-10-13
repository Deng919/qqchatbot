# Publication Stability Implementation Plan

> **For agentic workers:** Use executing-plans inline, following the user's existing choice to execute in this conversation.

**Goal:** Recover brief Windows report replacement conflicts without another model call and reject corrupt bridge text.

**Architecture:** Keep the existing prepared-write transaction; add one bounded replacement helper shared by install and rollback. Decode bridge stdout strictly and retain sanitized error propagation.

**Tech Stack:** Python, pytest, Windows filesystem, existing PowerShell bridge.

## Execution

- [x] Reuse attached worktree, fast-forward to `bff53b8`, create `codex/report-publish-stability-20261013`. Baseline reports/AI tests: 36 passed.
- [x] Add regression tests to `tests/test_reports.py`: inject PermissionError with winerror 5/32/33 into `.tmp` replacement, assert successful paired publication; keep failing JSON replacement, assert three attempts and restored old pair; transient `.bak` replacement during rollback must recover; generic PermissionError must not retry. Run tests before implementation.
- [x] In `qq_digest/reports.py`, import time; implement `_replace_with_retry(source, target)` with `for attempt in range(3)`, `os.replace` and return on success; on PermissionError raise when winerror not in `{5,32,33}` or attempt equals 2, otherwise `time.sleep(0.1 * (attempt + 1))`. Use helper in install and rollback. Re-run report and revision tests.
- [x] Correct GB18030 test outer `json.dumps(..., ensure_ascii=False)` in `tests/test_ai_client.py`. Add malformed UTF-8/GB18030 payload test using an invalid byte within JSON; assert AIError instead of a replacement character. Add success-with-binary-stderr regression.
- [x] In `qq_digest/ai/bridge.py` replace lossy fallback with strict GB18030 and sanitized AIError; merge nonzero/empty stdout handling, decoding stderr only to extract structured errors. Validate success stdout separately, preserving UTF-8-first behavior and existing error messages. Run bridge tests.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -o addopts='' -q` and existing `tests/*.cjs`; review diff and compile. Read-only compare Oct 12/13 report source counts with archive and record any mismatch.
- [ ] Commit and fast-forward local project branch. Build isolated desktop release under `D:\Apps`, verify manifest and run health smoke with isolated config (background jobs disabled), then update shortcut/registry. Record exact paths and limits. No remote publication.

## Verification record

- Targeted Python tests: `109 passed` across reports, bridge, revisions, manual summary, pipeline, and summary modules.
- Full Python suite: `1308 passed, 2 skipped, 1 warning`.
- JavaScript UI suite: `29 passed`.
- Read-only archive audit: report source counts matched messages available at each report's creation time. The October 12 reports have 111 additional messages in the archive now; they remain historical snapshots and were not regenerated in this bounded code change. October 13 reports matched the archive through their creation times.
- Desktop-focused tests after assigning the new release backend id: `76 passed, 1 skipped, 1 warning`.
