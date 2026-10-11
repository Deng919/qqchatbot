# Runtime Entry Deduplication Design

## Goal

Remove duplicated runtime orchestration between the Web application and CLI while preserving existing URLs, CLI commands, SQLite data, report files, provider behavior, and public-distribution restrictions.

## Scope

This phase covers three related entry-point problems:

1. Daily execution setup is duplicated in `qq_digest/web/app.py` and `qq_digest/cli.py`.
2. CLI `summarize` duplicates report generation and candidate transaction logic already implemented by the summary services.
3. Shared runtime resources are created and closed differently across entry points.

This phase does not split `archive.py`, change the SQLite schema, remove optional integrations, change Web routes, change report JSON/Markdown formats, or alter the semantics of refresh, retry, or operation locks.

## Proposed design

Add a small application-layer runner module responsible only for composing and executing existing services:

- Build the configured Collector, using `NTQQCollector` when NTQQ is enabled and `FixtureCollector` otherwise.
- Build the optional `BotConfig` from the existing configuration and environment variable.
- Build and close the configured AI provider through `build_ai_client`.
- Open and close a worker `Archive` connection for background work.
- Optionally refresh the NTQQ database before the daily run.
- Invoke the existing `DailyPipeline` or `ManualSummaryService` without moving their domain logic.

The runner returns small typed result values or existing service result values. It must not own HTTP responses, CLI formatting, scheduler state, or feature flags. The Web layer remains responsible for operation claims, scheduler bookkeeping, progress callbacks, and HTTP error mapping. The CLI remains responsible for terminal output and exit codes.

The Web daily scheduler and the CLI `run-daily` command will use the same daily runner. The CLI `summarize` command will use an existing summary service or a narrowly scoped service extracted from the current duplicate implementation; it will not reimplement report writing, candidate creation, or report reuse checks.

## Resource and failure behavior

- AI clients and worker archives are closed in `finally` blocks even when refresh, collection, AI, or report writing fails.
- Refresh failures are recorded as failed daily jobs by the Web scheduler, preserving its current preflight behavior.
- Per-group failures continue to be represented by `DailyRunResult.failed_groups` and do not change the existing partial-success status.
- Operation locking remains outside the runner so Web and CLI keep their current concurrency boundaries.
- No new background thread, retry policy, or database transaction boundary is introduced.

## Compatibility

- Preserve `qq-digest run-daily`, `qq-digest summarize`, `POST /api/run-daily`, automatic daily scheduling, and existing test injection points where practical.
- Preserve fixture mode when NTQQ is disabled.
- Preserve provider priority, public-distribution validation, API usage accounting, notification queue behavior, and knowledge paths.
- Keep the two untracked legacy templates and the activity lock in the original checkout untouched; they are not part of this refactor.

## Testing

Add tests before implementation for:

- runner construction in NTQQ and fixture modes;
- runner cleanup on refresh and pipeline failures;
- Web and CLI daily execution delegating to the same runner contract;
- CLI summary reusing the existing service behavior, including report reuse and candidate persistence;
- preservation of partial-success and preflight-failure results.

Run focused tests after each extraction, then the full Python suite and existing JavaScript/UI checks. Verification must include `git diff --check`, Python compilation, and route/CLI regression tests.

## Alternatives considered

### Split `Archive` first

This would provide stronger domain boundaries but requires changing dozens of direct SQL callers and risks transaction, migration, and legacy-data regressions. It is deferred.

### Only move files into new directories

This would change names without removing duplicated execution paths or reducing coupling. It provides little maintenance benefit and is rejected.

### Extract runtime composition first (chosen)

This removes the highest-confidence duplication at the entry boundary, keeps existing domain services and data contracts intact, and creates a stable seam for a later `archive.py` split.
