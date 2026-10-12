# Runtime Entry Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated daily and CLI summary orchestration while preserving current Web/CLI behavior, data contracts, report formats, and resource cleanup.

**Architecture:** Keep `DailyPipeline`, `ManualSummaryService`, `Archive`, and existing route boundaries as the domain services. Add one small `application_runner.py` for daily runtime composition and extend `DailyPipeline` with a summary-only entry point so CLI summary uses the existing publication path. Web and CLI remain adapters responsible for locks, status bookkeeping, formatting, and exit codes.

**Tech Stack:** Python 3.11+, FastAPI, Typer, SQLite, pytest, existing `DailyPipeline` and `Archive` services.

---

### Task 1: Lock the summary-only behavior before extraction

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`
- Test target: `qq_digest.pipeline.DailyPipeline.run_summary_only`

- [ ] **Step 1: Write the failing tests**

Add `test_summary_only_reuses_daily_report_without_collecting_or_creating_job` to seed one group and one archived message, use a Collector whose `collect` method raises if called, run the summary-only method twice, and assert that the first call creates one report, the second call reuses it, the jobs table remains empty, and the AI client is called once.

Add a second test that checks `window_mode="today"` uses the supplied `now` as the upper bound, so the refactor cannot silently switch CLI behavior to a full natural day.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_pipeline.py -k summary_only -q
```

Expected result: collection succeeds and the new tests fail because `DailyPipeline` has no `run_summary_only` method.

- [ ] **Step 3: Implement the minimal pipeline seam**

In `qq_digest/pipeline.py`:

1. Add `run_summary_only(self, now: datetime) -> DailyRunResult`.
2. Change the private processing method to accept `collect: bool = True` and `send_notifications: bool = True`.
3. When `collect` is false, read the existing archive window, skip Collector calls, skip ingest and sync-success markers, and keep `archive_mismatch=False`.
4. Keep the existing report fingerprint, candidate transaction, report writer, and per-group failure code unchanged.
5. Guard `_send_notifications` with `send_notifications` so CLI summary retains its current no-notification behavior.

The public `run_daily` path must continue to call the method with both flags enabled.

- [ ] **Step 4: Run the focused tests and existing pipeline tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_pipeline.py -k "summary_only or pipeline" -q
```

Expected result: all selected tests pass.

- [ ] **Step 5: Commit the pipeline seam**

```powershell
git add qq_digest/pipeline.py tests/test_pipeline.py tests/test_cli.py
git commit -m "refactor: expose archive-only daily summary path"
```

### Task 2: Add the shared daily application runner

**Files:**
- Create: `qq_digest/application_runner.py`
- Create: `tests/test_application_runner.py`

- [ ] **Step 1: Write failing runner tests**

Cover these behaviors with injected or monkeypatched factories: `test_build_collector_uses_fixture_when_ntqq_disabled`, `test_build_collector_uses_ntqq_when_enabled`, `test_run_daily_task_closes_ai_and_archive_after_pipeline_failure`, and `test_run_daily_task_records_preflight_refresh_failure`.

The cleanup test must use fake objects that record `close()` calls and a fake `DailyPipeline` that raises after construction. The preflight test must assert one failed `daily_digest` job with the target date and no pipeline construction.

- [ ] **Step 2: Run the new tests to verify they fail**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_application_runner.py -q
```

Expected result: failure because `qq_digest.application_runner` does not yet exist.

- [ ] **Step 3: Implement the runner**

Create `qq_digest/application_runner.py` with only these public functions: `build_collector(config) -> Collector`, `build_bot_config(config) -> BotConfig | None`, and `run_daily_task(config, *, scheduled_at=None, retry_of_job_id=None, refresh=True) -> dict`.

Implementation rules:

1. Use the existing `build_ai_client`, `Archive.open`, `DailyPipeline`, `NTQQCollector`, `FixtureCollector`, and `refresh_database` implementations.
2. Reproduce the Web preflight failure record with `Archive.start_job`/`finish_job` when refresh or provider setup fails before the pipeline starts.
3. Return the same keys currently assembled in `web.app._run_daily_task`: `success`, `status`, `report_date`, `groups_processed`, `messages_inserted`, `reports`, `candidates`, `succeeded_groups`, `skipped_groups`, and serialized `failed_groups` where available.
4. Close AI and worker archive resources in `finally`, including exceptions.
5. Do not claim operation locks, mutate scheduler state, format terminal output, or raise HTTP exceptions.
6. Keep `refresh` explicit so the Web scheduler can refresh (`True`) while the current CLI `run-daily` behavior can remain archive-driven (`False`) until separately changed.

- [ ] **Step 4: Run runner tests and compile the module**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_application_runner.py -q
D:\CodexTools\python\Scripts\python.exe -m py_compile qq_digest/application_runner.py
```

Expected result: all runner tests pass and compilation exits 0.

- [ ] **Step 5: Commit the runner**

```powershell
git add qq_digest/application_runner.py tests/test_application_runner.py
git commit -m "refactor: centralize daily runtime composition"
```

### Task 3: Switch Web and CLI daily execution to the runner

**Files:**
- Modify: `qq_digest/web/app.py:326-445`
- Modify: `qq_digest/cli.py:517-573`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Add delegation tests before changing adapters**

Update the CLI test to monkeypatch `qq_digest.application_runner.run_daily_task` and assert `run-daily` passes `refresh=False` and prints the returned partial-failure details.

Add a Web test that monkeypatches the same runner, exercises `POST /api/run-daily`, and asserts the Web adapter keeps its operation claim and maps a failed runner result to the existing HTTP error behavior.

- [ ] **Step 2: Run the new delegation tests and verify the expected failure**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py -k "run_daily" tests/test_web.py -k "run_daily" -q
```

Expected result: the tests fail because the adapters still construct the pipeline locally.

- [ ] **Step 3: Replace Web daily construction with the runner**

In `qq_digest/web/app.py`:

1. Remove the body of `_run_daily_task` that builds AI, refreshes NTQQ, creates the collector, opens the worker archive, and constructs `DailyPipeline`.
2. Keep a thin local adapter if needed for scheduler logging, but have it call `application_runner.run_daily_task(config, scheduled_at=scheduled_at, retry_of_job_id=retry_of_job_id, refresh=True)`.
3. Preserve scheduler state updates, catch-up handling, operation claims, and task cancellation exactly where they are.
4. Remove imports that become unused and retain route registration and lifespan behavior.

- [ ] **Step 4: Replace CLI `run-daily` construction with the runner**

In `qq_digest/cli.py`:

1. Load the config as before.
2. Set `scheduled_at = datetime.now(ZoneInfo(config.summary.timezone))` and call `run_daily_task(config, scheduled_at=scheduled_at, refresh=False)`.
3. Preserve the current Chinese output and non-fatal partial-group failure reporting.
4. Remove imports used only by the old local pipeline construction.

- [ ] **Step 5: Run focused adapter tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py tests/test_web.py -k "run_daily or partial_group" -q
```

Expected result: all selected tests pass.

- [ ] **Step 6: Commit the adapter switch**

```powershell
git add qq_digest/web/app.py qq_digest/cli.py tests/test_cli.py tests/test_web.py
git commit -m "refactor: reuse daily runner from web and cli"
```

### Task 4: Replace CLI `summarize` duplication with `DailyPipeline.run_summary_only`

**Files:**
- Modify: `qq_digest/cli.py:326-415`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add a delegation assertion**

Extend the existing legacy-report reuse test so it monkeypatches `DailyPipeline.run_summary_only`, records the supplied timezone-aware `now`, and verifies the command no longer imports or calls `GroupSummaryBuilder`/`ReportWriter` directly.

- [ ] **Step 2: Run the test and verify it fails**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py -k summarize -q
```

Expected result: the delegation assertion fails while the old inline implementation remains active.

- [ ] **Step 3: Implement the CLI adapter**

Replace the inline report loop with:

1. Load config and archive as before.
2. Seed groups as before.
3. Build one AI client and one `DailyPipeline` with the existing report directory, knowledge paths, attachment config, timezone, and max-context settings.
4. Use a fixture collector only as a construction placeholder; `run_summary_only` must not call it.
5. Print a compact result based on `DailyRunResult` while preserving the existing command exit behavior.
6. Close the AI client and archive in `finally`.

Do not call `run_daily`, do not create a daily job, and do not send notifications.

- [ ] **Step 4: Run CLI summary and pipeline regression tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_cli.py -k summarize tests/test_pipeline.py -q
```

Expected result: all selected tests pass, including legacy report regeneration and reuse.

- [ ] **Step 5: Commit the CLI summary delegation**

```powershell
git add qq_digest/cli.py tests/test_cli.py
git commit -m "refactor: reuse pipeline for cli summaries"
```

### Task 5: Full verification and documentation update

**Files:**
- Modify: `docs/PROJECT_REVIEW_2026-10-07.md` only if its current line counts or status claims are now inaccurate.
- Modify: `docs/ROADMAP.md` only to mark the completed runtime-entry portion of M-01/M-02 accurately; do not mark archive splitting complete.

- [ ] **Step 1: Run focused and integration checks**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_application_runner.py tests/test_pipeline.py tests/test_cli.py tests/test_web.py -q
```

- [ ] **Step 2: Run the full Python test suite**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest -q
```

Expected result: zero failures; report any pre-existing failure separately rather than masking it.

- [ ] **Step 3: Run static and packaging checks**

```powershell
D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest
git diff --check
git status --short
```

- [ ] **Step 4: Run existing JavaScript/UI checks**

Run the repository's CJS scripts under `tests/*.cjs` using the existing Node command convention and report each result. Do not add new UI behavior in this phase.

- [ ] **Step 5: Inspect the final diff against the design**

Confirm that no SQLite schema, URL, template, configuration, or untracked legacy file changed. Confirm that Web operation claims and scheduler state remain outside the runner.

- [ ] **Step 6: Commit documentation only if changed**

```powershell
git add docs/PROJECT_REVIEW_2026-10-07.md docs/ROADMAP.md
git commit -m "docs: record runtime entry deduplication"
```
