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

- [ ] Run the existing baseline:
  `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py tests/test_scheduler.py tests/test_operations.py tests/test_features.py tests/test_storage_handoff.py -o addopts='' -q`.
- [ ] Add real-archive fixtures and a one-cycle sleep driver, keeping replacement
  time local to these tests. Each test imports the new module inside its body so
  missing-module failures are visible rather than preventing collection:

```python
def test_daily_respects_live_pause(runtime, monkeypatch):
    from qq_digest.web import background_tasks as background
    # runtime owns a temporary Archive, Config, FeatureService and coordinator.
    # Drive two cycles: first disabled, then enabled; assert only the second
    # enters daily work and the shared dictionary reflects its result.
```

- [ ] Test persisted daily catch-up date/result handling, current-date retry
  limits and operation admission; use real scheduler queries and seeded jobs.
- [ ] Test setup/feature/running pauses; test running flag and claim release after
  worker error and cancellation. A blocking physical worker must retain its claim
  until released after cancellation.
- [ ] Test collection startup/intervals, live enable/disable, saved result and
  failure cleanup; test the notification queue continues after a logged error.
- [ ] Test lifespan with/without bot: replace external listeners and workers,
  assert startup, listener stop before cancellation, all tasks awaited and topic
  closure after shutdown. Include an app integration test for manual requests
  observing background running/result state.
- [ ] Run the new test file with `-o addopts='' -q`. Confirm expected failures
  from the absent module before writing production code.

## Task 2: Move loops and lifecycle

**Create:** `qq_digest/web/background_tasks.py`.
**Modify:** `qq_digest/web/app.py`, `tests/test_web.py`.

- [ ] Copy the original loop bodies from baseline lines 383-511, dedent Python
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

- [ ] Move the existing lifespan body into this factory; keep internal bot state
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

- [ ] Replace the old nested loops/lifespan in `create_app` with:

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

- [ ] Run the new tests and the baseline suite plus worker, history-inspection,
  first-use, notification and sync tests. Resolve actual failures within scope.
- [ ] Compare moved loop AST bodies against baseline after callback renaming;
  compare all existing route bodies, startup state restoration and callback
  definitions unchanged. Inspect lifecycle differences explicitly.
- [ ] Commit extraction and tests: `refactor: isolate web background tasks`.

## Task 3: Review and integrate

- [ ] Use requesting-code-review for an independent read-only review against
  `90f1d76`; address verified issues before integration.
- [ ] Run full Python suite with `-o addopts='' -q`; report skips/warnings.
- [ ] Run all `tests/*.cjs`, checking every exit code; run compileall and
  `git diff --check 90f1d76`.
- [ ] Update this plan with exact results. Record the M-02 implementation scope
  in `docs/ROADMAP.md`; do not mark unrelated M-01/M-03 goals complete.
- [ ] Check original checkout tracked changes/HEAD, fast-forward to verified
  implementation, compare tree identity and run focused integration there.
- [ ] Record integration, commit documentation and fast-forward again. Preserve
  untracked files and report completion plus validation limits.
