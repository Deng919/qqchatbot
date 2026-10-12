# Web background task extraction

Baseline: `90f1d76`. The user approved stage three and autonomous execution.

## Problem and constraints

`qq_digest/web/app.py` has 1,920 lines. Three periodic loops and their lifecycle
are nested among route assembly and handlers. Their dependencies are implicit
closure variables. The problem is locating and testing background orchestration;
this stage does not change schedules or add features.

Preserve URLs, schema, configuration, task intervals, exception handling, startup
order, stop order, cancellation behavior, job queries and feature checks. Preserve
the existing worker guard so cancellation cannot release operation claims before
physical threads finish. Do not open real user data or send external requests.

## Chosen boundary

Create `qq_digest/web/background_tasks.py` with ordinary functions:

- `daily_scheduler_loop(config, archive, operations, feature_service,
  scheduler_state, run_daily_task)` contains the existing daily/catch-up loop.
- `message_sync_loop(config, operations, feature_service, sync_scheduler_state)`
  contains the existing automatic collection loop.
- `notification_queue_loop(config, operations, notifier)` delegates to the
  existing guarded notification worker and preserves retry logging.
- `background_lifespan(...)` returns the FastAPI lifespan context, starting and
  stopping the bot listener, notification queue, daily loop, collection loop,
  history inspection and reminders in their existing order. It reads the already
  assembled feature service and reminder sink from `app.state` at startup.

Keep the daily runtime callback, scheduler state creation/restoration and all
manual route handlers in `app.py`. Pass the existing daily/sync dictionaries by
reference: manual requests, retries, `/api/scheduler` and background work must
observe the same objects. Bot listener/notification lifecycle state moves with
its lifecycle because no route consumes it.

Alternative: a scheduler manager with typed state models would expand the state
contract and require route rewrites without a demonstrated benefit. Separate
modules for each loop would scatter a single small lifecycle. One module with
four functions is sufficient; no registries, new clocks, generic task frameworks,
new public state objects or configuration switches.

## Behavior contracts

Daily work sleeps 60 seconds before checking; respects setup pause, auto_daily,
running state and operation admission. Catch-up retains its separate persisted
attempt budget and result freshness check. Normal execution uses persisted retry
state and sets last_run_date only on success. Running flags clear on exceptions
and cancellation; existing uncaught failures remain uncaught.

Collection retains configured startup delay and intervals, collection/NTQQ flags,
setup pause, auto_collection and operation claim. Configuration and feature values
are read live on every iteration. Notifications retain worker-owned claims and
60-second retry cycles after logged exceptions.

Lifespan retains conditional bot construction, all existing task handles, listener
stop before task cancellation, gather with return_exceptions, then guarded topic
tracking closure. This extraction does not redesign historical lifecycle failure
handling or reminder cancellation semantics.

## Verification and delivery

Use existing web, scheduler, operations, features and storage-handoff tests as a
baseline. Add deterministic tests that drive the real extracted loops with real
SQLite/FeatureService/OperationCoordinator, replacing time and external workers
only. Cover catch-up, due/limited retries, live pauses, admission races, sync
results, worker errors/cancellation, notification retry and lifecycle shutdown.
Preserve existing route tests; relocate only the scheduler-specific monkeypatch
target when it moves modules.

Review the moved AST bodies against baseline with explicit parameter/name
substitutions. Run related tests, full Python regression, all CJS checks,
compileall and cumulative diff checks. Obtain an independent read-only review.
Fast-forward the original branch only after verification; preserve the activity
lock and untracked legacy templates. Keep the existing managed worktree at
`C:/Users/xun/.codex/worktrees/runner-dedup/chatbot` for this work. No desktop
packaging, publishing or real QQ/AI/notification end-to-end run in this stage.
