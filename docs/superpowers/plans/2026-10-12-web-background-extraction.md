# Web Background Extraction Implementation Plan

> **For agentic workers:** Use executing-plans to implement in the current session, as the user requested. Steps use checkbox syntax for tracking.

**Goal:** Isolate background orchestration without changing existing behavior.

**Architecture:** Move the three loops and the lifespan context into one module.
Pass existing services and daily/sync state dictionaries; retain worker and route
contracts. No scheduler class or new state model.

**Tech Stack:** Python, asyncio, FastAPI, SQLite, pytest and Node CJS checks.

Spec: `docs/superpowers/specs/2026-10-12-web-background-extraction-design.md`.
Worktree: `C:/Users/xun/.codex/worktrees/runner-dedup/chatbot`.
Baseline: `90f1d76`. User requested autonomous execution without routine approvals.

## Task 1: Characterize the loop boundary before extraction

**Create:** `tests/test_web_background_tasks.py`.

- [x] Run the existing baseline:
  `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py tests/test_scheduler.py tests/test_operations.py tests/test_features.py tests/test_storage_handoff.py -o addopts='' -q`.
- [x] Add real-archive fixtures and a one-cycle sleep driver, keeping replacement
  time local to these tests. Each test imports the new module inside its body so
  missing-module failures are visible rather than preventing collection:

```python
def test_daily_respects_live_pause(runtime, monkeypatch):
    from qq_digest.web import background_tasks as background
    # runtime owns a temporary Archive, Config, FeatureService and coordinator.
    # Drive two cycles: first disabled, then enabled; assert only the second
    # enters daily work and the shared dictionary reflects its result.
```

- [x] Test persisted daily catch-up date/result handling, current-date retry
  limits and operation admission; use real scheduler queries and seeded jobs.
- [x] Test setup/feature/running pauses; test running flag and claim release after
  worker error and cancellation. A blocking physical worker must retain its claim
  until released after cancellation.
- [x] Test collection startup/intervals, live enable/disable, saved result and
  failure cleanup; test the notification queue continues after a logged error.
- [x] Test lifespan with/without bot: replace external listeners and workers,
  assert startup, listener stop before cancellation, all tasks awaited and topic
  closure after shutdown. Include an app integration test for manual requests
  observing background running/result state.
- [x] Run the new test file with `-o addopts='' -q`. Confirm expected failures
  from the absent module before writing production code.

## Task 2: Move loops and lifecycle

**Create:** `qq_digest/web/background_tasks.py`.
**Modify:** `qq_digest/web/app.py`, `tests/test_web.py`.

- [x] Copy the original loop bodies from baseline lines 383-511, dedent Python
  while preserving string contents, and give explicit parameters:

```python
async def daily_scheduler_loop(config, archive, operations, feature_service,
                               scheduler_state, run_daily_task):
    # Original _daily_scheduler_loop body; _run_daily_task becomes run_daily_task.

async def message_sync_loop(config, operations, feature_service,
                            sync_scheduler_state):
    # Original _message_sync_loop body.

async def notification_queue_loop(config, operations, notifier):
    # Original _notification_queue_loop body.
```

These comments describe moving the baseline bodies, not replacement no-op code.
Import asyncio, datetime, ZoneInfo, scheduler functions, setup_in_progress,
OperationBusy and guarded_to_thread in the destination. Keep worker imports,
exception branches, SQL, sleeps and assignments unchanged.

- [x] Move the existing lifespan body into this factory; keep internal bot state
  dictionaries outside the returned context as in the original app closure:

```python
def background_lifespan(*, config, archive, candidates, knowledge, operations,
                        scheduler_state, sync_scheduler_state, run_daily_task):
    ws_state = {"listener": None, "task": None}
    notification_state = {"notifier": None, "task": None}

    @asynccontextmanager
    async def lifespan(app):
        feature_service = app.state.features
        # Original lifespan body, calling extracted functions with services/state.
    return lifespan
```

- [x] Replace the old nested loops/lifespan in `create_app` with:

```python
    lifespan = background_lifespan(
        config=config, archive=archive, candidates=candidates, knowledge=knowledge,
        operations=operations, scheduler_state=scheduler_state,
        sync_scheduler_state=sync_scheduler_state, run_daily_task=_run_daily_task,
    )
```

Remove the now-unused bot state declarations and imports from `app.py`; retain
`run_at_for_report_date` because the manual retry route uses it. In the existing
live-switch test move `pending_catchup_date` monkeypatch from `web.app` to
`web.background_tasks`; no semantic assertion changes.

- [x] Run the new tests and the baseline suite plus worker, history-inspection,
  first-use, notification and sync tests. Resolve actual failures within scope.
- [x] Compare moved loop AST bodies against baseline after callback renaming;
  compare all existing route bodies, startup state restoration and callback
  definitions unchanged. Inspect lifecycle differences explicitly.
- [x] Commit extraction and tests: `refactor: isolate web background tasks`.

## Task 3: Review and integrate

- [x] Use requesting-code-review for an independent read-only review against
  `90f1d76`; address verified issues before integration.
- [x] Run full Python suite with `-o addopts='' -q`; report skips/warnings.
- [x] Run all `tests/*.cjs`, checking every exit code; run compileall and
  `git diff --check 90f1d76`.
- [x] Update this plan with exact results. Record the M-02 implementation scope
  in `docs/ROADMAP.md`; do not mark unrelated M-01/M-03 goals complete.
- [x] Check original checkout tracked changes/HEAD, fast-forward to verified
  implementation, compare tree identity and run focused integration there.
- [x] Record integration, commit documentation and fast-forward again. Preserve
  untracked files and report completion plus validation limits.

## Verification record

- Baseline suite: 141 passed, one existing Starlette/httpx deprecation warning.
- Before extraction, all 26 initial new cases failed because the new module was absent; the added route integration case also failed for the same reason.
- After extraction, all 27 new cases passed in 11.61s.
- Expanded related regression: 207 passed, one warning, in 90.78s. This includes worker lifecycle, history inspection, first-use, notification and sync tests.
- AST comparison confirmed all three loop bodies identical after callback renaming and the lifecycle identical after explicit loop bindings. All retained create_app statements/routes and state restoration remain identical.
- 29 CJS scripts passed; compileall and cumulative diff checks passed.
- Original checkout baseline: 90f1d76, no tracked changes; existing three untracked files retained.
- Independent read-only review of `90f1d76..3a56d75`: no actionable findings. It independently confirmed shared-state identity, AST preservation, dependencies and shutdown ordering.
- Full Python regression: 1,296 passed, 2 skipped, 1 existing Starlette/httpx deprecation warning in 436.50s. Rerunning the skipped cases with `-rs` confirmed missing Windows symlink creation permission.
- Original branch `codex/public-beta-2026-10-06` fast-forwarded from `90f1d76` to implementation `3a56d75`. Original/worktree committed tree identity matched: `5ebe7202e2096c994141e35bea2a539c506deb84` before this documentation-only update.
- Post-merge regression: 31 passed, one existing warning in 13.78s. Includes every new background case and existing live feature switch, scheduler API and persisted restart-state checks.
- Compilation and cumulative diff checks passed on the original checkout. The activity lock and two untracked legacy templates have identical SHA-256 values before/after integration; no tracked local edits remain there.
- `docs/ROADMAP.md` records M-02 as implemented; M-01/M-03 remain open. State initialization/restoration stays beside the existing route consumers, as specified.
- Original checkout: `D:/Desktop/AI/chatbot`; managed worktree retained at `C:/Users/xun/.codex/worktrees/runner-dedup/chatbot`.
- Desktop packaging and real QQ/AI/notification end-to-end validation were not performed.
