# Manual Range Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Web workflow that creates one persisted summary per selected group for an archive-only date range of at most seven natural days.

**Architecture:** A new `ManualSummaryService` validates requests, reads left-closed/right-open archive windows, and isolates failures per group. A shared group-summary builder is used by both daily and manual flows; report persistence uses prepared file pairs plus a database transaction so controlled failures preserve the previous report. Daily and range reports remain in separate tables but are normalized by the Web API.

**Tech Stack:** Python 3.11+, SQLite, Pydantic 2, FastAPI, Jinja2, vanilla JavaScript/CSS, pytest.

**Execution constraint:** Work directly in the current workspace, preserve unrelated dirty files, and do not stage or commit implementation files.

---

## File map

- Create `qq_digest/group_summary.py`: shared per-group summary generation, Markdown rendering inputs, and candidate draft conversion.
- Create `qq_digest/manual_summary.py`: request validation, date-window conversion, reuse logic, per-group orchestration, job tracking, and range report publication.
- Create `tests/test_manual_summary.py`: service-level behavior and failure isolation.
- Modify `qq_digest/archive.py`: `manual_reports` migration, half-open message query, range report CRUD, unified report listing, and group cleanup.
- Modify `qq_digest/candidates.py`: transaction-aware candidate insertion used by atomic report publication.
- Modify `qq_digest/reports.py`: range headings, stable range filenames, and prepared write rollback lifecycle.
- Modify `qq_digest/pipeline.py`: delegate summary-to-report content construction to the shared builder while preserving current daily behavior.
- Modify `qq_digest/web/operations.py`: add `manual_summary` to the global conflict set.
- Modify `qq_digest/web/app.py`: request model, worker, range endpoint, unified list/detail endpoints, and safe error mapping.
- Modify `qq_digest/web/templates/reports.html`: range-summary drawer, multiselect/date/detail controls, normalized report rows, and result feedback.
- Modify `tests/test_archive.py`, `tests/test_reports.py`, `tests/test_pipeline.py`, `tests/test_operations.py`, and `tests/test_web.py`: focused regression and integration coverage.

### Task 1: Add archive primitives for range reports

**Files:**
- Modify: `qq_digest/archive.py`
- Test: `tests/test_archive.py`

- [ ] **Step 1: Write failing migration and half-open-window tests**

Add tests that open a fresh archive, inspect `PRAGMA table_info(manual_reports)`, and verify the unique identity fields exist. Add a boundary test with messages exactly at `start`, immediately before `end`, and exactly at `end`:

```python
def test_messages_in_window_excludes_right_boundary(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    start = datetime(2026, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=1)
    archive.ingest([
        make_message("start", start),
        make_message("inside", end - timedelta(seconds=1)),
        make_message("next-day", end),
    ])

    assert [m.msg_id for m in archive.messages_in_window(123, start, end)] == [
        "start", "inside"
    ]
```

Add a CRUD test for `record_manual_report`, `manual_report_for`, and `manual_report_by_id`. Re-record the same `(group_id, start_date, end_date, detail_mode)` and assert the ID is stable, `updated_at` changes, and `created_at` remains stable.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_archive.py -q
```

Expected: failures because `manual_reports`, `messages_in_window`, and manual report methods do not exist.

- [ ] **Step 3: Add the schema and archive methods**

Add this table in `_migrate()`:

```sql
CREATE TABLE IF NOT EXISTS manual_reports (
    manual_report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    detail_mode TEXT NOT NULL,
    effective_template TEXT NOT NULL,
    markdown_path TEXT NOT NULL,
    json_path TEXT NOT NULL,
    candidate_ids TEXT NOT NULL DEFAULT '[]',
    input_fingerprint TEXT NOT NULL,
    source_message_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(group_id, start_date, end_date, detail_mode)
);
CREATE INDEX IF NOT EXISTS idx_manual_reports_window
    ON manual_reports(end_date DESC, start_date DESC, group_id);
```

Implement the half-open query separately so existing daily inclusive semantics remain compatible:

```python
def messages_in_window(self, group_id: int, start: datetime, end: datetime) -> list[NormalizedMessage]:
    rows = self.connection.execute(
        """SELECT * FROM messages
           WHERE group_id=? AND timestamp>=? AND timestamp<?
           ORDER BY timestamp, msg_id""",
        (group_id, self._utc_timestamp(start), self._utc_timestamp(end)),
    ).fetchall()
    return [self._row_to_message(row) for row in rows]
```

Add `manual_report_for(group_id, start_date, end_date, detail_mode)`, `manual_report_by_id(manual_report_id)`, and an uncommitted `record_manual_report_in_transaction(group_id, start_date, end_date, detail_mode, effective_template, markdown_path, json_path, candidate_ids, input_fingerprint, source_message_count)` helper. The upsert must keep the original `created_at`, update `updated_at`, and return `manual_report_id`.

- [ ] **Step 4: Extend group deletion**

Query both `reports` and `manual_reports` before deletion, include both file pairs in `GroupDeletionResult.report_paths`, and delete `manual_reports` inside the same database transaction before deleting the group.

- [ ] **Step 5: Run archive tests**

Run the Task 1 command again. Expected: all `tests/test_archive.py` tests pass.

### Task 2: Add transaction-aware candidates and prepared report writes

**Files:**
- Modify: `qq_digest/candidates.py`
- Modify: `qq_digest/reports.py`
- Test: `tests/test_candidates.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write failing candidate transaction tests**

Add a test that opens `with archive.transaction():`, calls `candidates.create_in_transaction(group_id=123, created_date="2026-09-01", candidate_type="resource", title="站点", link="", reason="有价值", excerpt="原文", message_ids=["m1"])`, then raises an exception. Assert the candidate count remains zero. Add a successful case that returns the inserted ID without committing internally.

- [ ] **Step 2: Implement a non-nesting candidate insert**

Extract current SQL into `_create(kwargs, now)`, retain `create(**kwargs)` as the committing public method, and add:

```python
def create_in_transaction(self, **kwargs) -> int:
    return self._create(kwargs, datetime.now(timezone.utc).isoformat())
```

`create()` becomes:

```python
def create(self, **kwargs) -> int:
    with self.archive.transaction():
        return self.create_in_transaction(**kwargs)
```

The validation and deduplication query must remain identical for both paths.

- [ ] **Step 3: Write failing prepared-write tests**

Test a new `ReportWriter.prepare_named(stem, markdown, payload)` lifecycle:

```python
prepared = writer.prepare_named("range__2026-09-01__2026-09-07__123__group", "new", {"v": 2})
prepared.install()
prepared.rollback()
assert old_markdown.read_text(encoding="utf-8") == "old"
assert json.loads(old_json.read_text(encoding="utf-8")) == {"v": 1}
```

Also verify `install(); finalize()` keeps the new pair and removes all `.tmp`/`.bak` files.

- [ ] **Step 4: Implement `PreparedReportWrite`**

Add a small object with explicit states and methods. Its state transitions must use the following concrete shape:

```python
@dataclass
class PreparedReportWrite:
    paths: ReportPaths
    temporary_paths: dict[Path, Path]
    backup_paths: dict[Path, Path] = field(default_factory=dict)
    installed_paths: set[Path] = field(default_factory=set)

    def install(self) -> None:
        for target in self.temporary_paths:
            if target.exists():
                backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
                shutil.copy2(target, backup)
                self.backup_paths[target] = backup
        try:
            for target, temporary in self.temporary_paths.items():
                os.replace(temporary, target)
                self.installed_paths.add(target)
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        for target in reversed(tuple(self.installed_paths)):
            backup = self.backup_paths.get(target)
            if backup is not None and backup.exists():
                os.replace(backup, target)
            else:
                target.unlink(missing_ok=True)
        self.finalize()

    def finalize(self) -> None:
        for temporary in self.temporary_paths.values():
            temporary.unlink(missing_ok=True)
        for backup in self.backup_paths.values():
            backup.unlink(missing_ok=True)
```

`prepare_named()` writes and fsyncs both temporary files without touching published targets. `install()` creates backups and replaces both targets. `rollback()` restores backups or removes newly installed targets. `finalize()` removes temporary files and backups. Make all cleanup idempotent.

Keep `ReportWriter.write()` backward compatible by implementing it through `prepare_named()` with the existing `<report_date>__<group_id>` stem.

- [ ] **Step 5: Add range rendering and filename coverage**

Extend `render_markdown` with backward-compatible keyword arguments:

```python
title_suffix: str = "日报"
date_label: str = "日期"
```

Use `# {group_name}{title_suffix}` and `- {date_label}：{report_date}`. Test a range report with `title_suffix="范围摘要"` and `date_label="日期范围"`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_candidates.py tests/test_reports.py -q
```

Expected: all focused tests pass, including all pre-existing atomic pair tests.

### Task 3: Extract shared group-summary construction

**Files:**
- Create: `qq_digest/group_summary.py`
- Modify: `qq_digest/pipeline.py`
- Test: `tests/test_pipeline.py`
- Create: `tests/test_group_summary.py`

- [ ] **Step 1: Write failing shared-builder tests**

Create tests for `GroupSummaryBuilder.build(group=group, template="concise", window_start=start, window_end=end, report_date="2026-09-01 至 2026-09-07", candidate_date="2026-09-07", messages=messages, timezone=timezone, knowledge_base="", report_kind="range")`. Verify the result contains `markdown`, `payload`, and `candidate_kwargs`; range mode uses “范围摘要/日期范围”, daily mode keeps the current title, task formatting remains unchanged, candidate `created_date` remains strict ISO, candidate excerpts come from referenced message IDs, and the data-quality sentence contains source/included/discarded counts plus the truncation warning.

- [ ] **Step 2: Implement the builder**

Define focused immutable outputs:

```python
@dataclass(frozen=True)
class GroupSummaryArtifact:
    markdown: str
    payload: dict
    candidate_kwargs: Sequence[dict]
    source_message_count: int

class GroupSummaryBuilder:
    def __init__(self, summarizer: Summarizer):
        self.summarizer = summarizer

    def build(self, *, group: GroupConfig, template: str, window_start: datetime,
              window_end: datetime, report_date: str, candidate_date: str,
              messages: list[NormalizedMessage], timezone: ZoneInfo,
              knowledge_base: str, report_kind: str) -> GroupSummaryArtifact:
        summary = self.summarizer.summarize(
            group_id=group.group_id,
            group_name=group.name,
            category=group.category,
            template=template,
            keywords=group.keywords,
            window_start=window_start,
            window_end=window_end,
            messages=messages,
            timezone=timezone,
            knowledge_base=knowledge_base,
        )
        quality_note = (
            f"原始 {summary.source_messages} 条，纳入 {summary.included_messages} 条，"
            f"清洗丢弃 {summary.discarded_messages} 条。"
            + (
                f"上下文达到上限，仅保留最近 {summary.context_chars} 字符。"
                if summary.context_truncated else ""
            )
        )
        markdown = render_markdown(
            group_name=group.name,
            report_date=report_date,
            window=f"{window_start.isoformat()} 到 {window_end.isoformat()}",
            topics=[item.model_dump() for item in summary.response.main_topics],
            conclusions=summary.response.conclusions,
            resources=[item.model_dump() for item in summary.response.resources],
            tasks=[
                f"{item.owner}：{item.description}"
                + (f"（{item.deadline}）" if item.deadline else "")
                for item in summary.response.tasks
            ],
            open_questions=summary.response.open_questions,
            deterministic=summary.deterministic,
            quality_note=quality_note,
            title_suffix="范围摘要" if report_kind == "range" else "日报",
            date_label="日期范围" if report_kind == "range" else "日期",
        )
        candidate_kwargs = tuple(
            {
                "group_id": group.group_id,
                "created_date": candidate_date,
                "candidate_type": candidate.type,
                "title": candidate.title,
                "link": candidate.link,
                "content": candidate.content,
                "reason": candidate.reason,
                "excerpt": next(
                    (message.text[:100] for message in messages
                     if message.msg_id in candidate.message_ids),
                    "",
                ),
                "message_ids": candidate.message_ids,
            }
            for candidate in summary.response.candidates
        ) if group.important_candidates else ()
        return GroupSummaryArtifact(
            markdown=markdown,
            payload=summary.response.model_dump(),
            candidate_kwargs=candidate_kwargs,
            source_message_count=len(messages),
        )
```

The builder owns only content construction. It performs no archive writes, file writes, notifications, or job tracking.

- [ ] **Step 3: Refactor daily pipeline to call the builder**

Keep `DailyPipeline.summarizer` public for current tests, construct `GroupSummaryBuilder(self.summarizer)` at call time, and replace the duplicated `summarize`/Markdown/candidate-kwargs block with the artifact. Preserve daily report filenames, report records, candidate behavior, notifications, and partial-success semantics.

- [ ] **Step 4: Verify daily behavior**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_group_summary.py tests/test_pipeline.py tests/test_summary.py tests/test_reports.py -q
```

Expected: all tests pass and existing daily output remains compatible.

### Task 4: Implement `ManualSummaryService`

**Files:**
- Create: `qq_digest/manual_summary.py`
- Create: `tests/test_manual_summary.py`

- [ ] **Step 1: Write request-validation tests**

Cover empty group IDs, duplicates, invalid date ordering, eight natural days, invalid `detail_mode`, disabled/missing groups, and a valid seven-day range. Assert the timezone conversion returns local midnight for the first day and midnight after the final day.

- [ ] **Step 2: Define request and result types**

Use dataclasses with explicit literals:

```python
DetailMode = Literal["group", "concise", "detailed"]

@dataclass(frozen=True)
class ManualSummaryRequest:
    group_ids: Sequence[int]
    start_date: date
    end_date: date
    detail_mode: DetailMode = "group"

@dataclass(frozen=True)
class ManualReportResult:
    report_key: str
    group_id: int
    group_name: str
    start_date: str
    end_date: str
    effective_template: str

@dataclass
class ManualSummaryResult:
    status: str
    created_reports: list[ManualReportResult]
    reused_reports: list[ManualReportResult]
    skipped_groups: list[dict]
    failed_groups: list[GroupRunFailure]
```

Provide one parser that converts strict ISO strings and rejects ranges longer than seven inclusive days.

- [ ] **Step 3: Write orchestration tests**

Use two groups and archived messages to verify one report per group, no collector dependency, correct per-group template resolution, temporary overrides, candidate creation, no send-log rows, stable reuse, regeneration after a new archived message, no-message skips, and continued processing after one AI failure.

- [ ] **Step 4: Implement generation and reuse**

For each validated group:

```python
messages = archive.messages_in_window(group.group_id, start, end_exclusive)
effective_template = group.template if request.detail_mode == "group" else request.detail_mode
fingerprint = message_fingerprint(messages)
existing = archive.manual_report_for(
    group.group_id,
    request.start_date.isoformat(),
    request.end_date.isoformat(),
    request.detail_mode,
)
if existing and existing["input_fingerprint"] == fingerprint and existing["effective_template"] == effective_template:
    reused_reports.append(to_result(existing, group))
    continue
```

Generate via `GroupSummaryBuilder` only when needed, passing `candidate_date=request.end_date.isoformat()`. Use a stable stem:

```python
stem = f"range__{request.start_date}__{request.end_date}__{group.group_id}__{request.detail_mode}"
```

- [ ] **Step 5: Implement atomic publication and job tracking**

Prepare files first. Then, in one `archive.transaction()`, create candidates with `create_in_transaction`, upsert `manual_reports`, delete only old pending candidates, and call `prepared.install()` before the transaction exits. On any exception call `prepared.rollback()`; after database commit call `prepared.finalize()`.

Wrap the whole run in a `manual_summary` job. Store sanitized per-group failures as JSON for `partial_success`; use `failed` only for request-wide failures before per-group processing. Never invoke the notifier.

- [ ] **Step 6: Run manual service tests**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_manual_summary.py -q
```

Expected: all manual service tests pass.

### Task 5: Integrate operation conflicts and the Web API

**Files:**
- Modify: `qq_digest/web/operations.py`
- Modify: `qq_digest/web/app.py`
- Modify: `tests/test_operations.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing operation-conflict tests**

Add `manual_summary` to parameterized conflicts against every operation, and update the released snapshot assertion with `manual_summary_running: False`.

- [ ] **Step 2: Add the operation kind**

Change the tuple to:

```python
OPERATION_KINDS = ("daily", "refresh", "collect", "sync", "manual_summary")
```

Keep the existing all-to-all conflict construction.

- [ ] **Step 3: Write failing range API tests**

Test authentication, valid request, strict 422 responses, missing/disabled groups, a conflict held by `operations.claim("sync")`, partial success, and resource cleanup. Monkeypatch `qq_digest.ai.factory.build_ai_client` with deterministic fake clients and assert `close()` is called.

- [ ] **Step 4: Add the request model and worker**

Define the FastAPI body model outside `create_app`:

```python
class ManualRangePayload(BaseModel):
    group_ids: list[int]
    start_date: date
    end_date: date
    detail_mode: Literal["group", "concise", "detailed"] = "group"
```

Add a synchronous worker that opens a fresh `Archive` at `cfg.archive_path`, builds the configured AI client, creates `ManualSummaryService`, returns dataclass results as JSON-safe dictionaries, and closes both AI and archive in `finally`.

- [ ] **Step 5: Add `POST /api/reports/range`**

Require login and configuration, claim `manual_summary`, run the worker through `asyncio.to_thread`, map `OperationBusy` to the existing structured 409 response, map service `ValueError` to 422, and return partial success with HTTP 200.

- [ ] **Step 6: Normalize report list and details**

Return both tables from `GET /api/reports` with these fields:

```python
{
    "report_kind": "daily" | "range",
    "report_id": int,
    "report_key": f"{kind}:{id}",
    "window_start_date": str,
    "window_end_date": str,
    "effective_template": str,
    "group_name": str,
    "candidate_count": int,
    "created_at": str,
}
```

Add `GET /api/reports/{report_kind}/{report_id}` with a literal kind check, group-scoped table lookup, file existence fallback, and range metadata. Keep the old numeric detail route unchanged.

- [ ] **Step 7: Run operation and Web API tests**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_operations.py tests/test_web.py -q
```

Expected: all focused tests pass.

### Task 6: Build the reports-page range drawer

**Files:**
- Modify: `qq_digest/web/templates/reports.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing markup contract tests**

Assert the page uses “摘要报告”, contains `range-summary-panel`, `range-group-search`, checkbox-backed group selection, `range-start-date`, `range-end-date`, `range-detail-mode`, selection summary, submit button, close button, and no inline insertion of unescaped group names.

- [ ] **Step 2: Add the drawer structure and defaults**

Rename “立即生成摘要” to “生成今日日报”. Add a secondary “生成范围摘要” button and an accessible backdrop/drawer. On load, set both dates to today in local browser time, select `group`, fetch `/api/groups`, and render only enabled groups with DOM APIs or `escapeHtml`.

- [ ] **Step 3: Implement validation and submission**

Before submission, calculate inclusive days and enforce 1–7. Send:

```javascript
api('POST', '/api/reports/range', {
  group_ids: selectedGroupIds(),
  start_date: start.value,
  end_date: end.value,
  detail_mode: detail.value
})
```

Disable the drawer controls during the request. Render a concise toast containing created, reused, skipped, and failed counts. Keep the drawer open on validation/server error, and refresh reports after a completed request.

- [ ] **Step 4: Normalize list rendering**

Add columns for type, date/range, group, template, candidates, update time, and action. Use `report_key` only as data, and call the typed detail endpoint using separately validated `report_kind` and numeric `report_id`. Show `日报`/`范围` and `精简`/`详细` badges.

- [ ] **Step 5: Add responsive styles and keyboard behavior**

Desktop: fixed right drawer with backdrop. Mobile: `inset` the panel within the viewport and allow internal scrolling. Escape closes the drawer, backdrop click closes when idle, and focus returns to the opener.

- [ ] **Step 6: Run Web tests**

Run the Task 5 Web test command again. Expected: all operation and Web tests pass.

### Task 7: Full regression and static verification

**Files:**
- Verify all changed production and test files.

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest -q
```

Expected: all tests pass; the existing Starlette/httpx deprecation warning may remain, with no new warnings.

- [ ] **Step 2: Compile the package**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest tests
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Check the diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors. Confirm only intended files changed in this feature and all unrelated pre-existing changes remain untouched. Do not stage or commit implementation files.

### Task 8: Real-browser verification

**Files:**
- Verify: `qq_digest/web/templates/reports.html`
- Verify: local Web application behavior.

- [ ] **Step 1: Start an isolated QA server**

Use a disposable configuration under the existing ignored `work/` directory with a copied test archive and fake/deterministic AI provider. Do not run against live NTQQ data, live notifications, or the user's production report directory.

- [ ] **Step 2: Verify desktop behavior**

At 1440×900, log in, open `/reports`, open the range drawer, search/select multiple groups, change dates and detail mode, submit, verify button disabling and result feedback, then open generated range details.

- [ ] **Step 3: Verify mobile behavior**

At 390×844, repeat opening, selection, validation, submission, list refresh, and detail viewing. Confirm no horizontal viewport overflow and all controls remain reachable.

- [ ] **Step 4: Inspect browser diagnostics**

Verify console and network panels show no new errors, failed asset requests, unsafe HTML rendering, or duplicate submissions.

- [ ] **Step 5: Stop the isolated QA server**

Stop only the exact QA process started in Step 1. Leave the user's existing live server and browser tab unchanged.

## Completion checklist

- [ ] Archive migration is additive and preserves existing reports.
- [ ] Manual range requests are archive-only, per-group, and limited to seven inclusive days.
- [ ] Daily behavior remains compatible after shared builder extraction.
- [ ] Reuse, safe regeneration, candidate lifecycle, and no-notification behavior are verified.
- [ ] All operation conflicts and authenticated Web routes are covered.
- [ ] Reports UI works at desktop and mobile sizes.
- [ ] Full automated verification passes with no new warnings.
- [ ] No implementation files are staged or committed.
