# QQ Digest P0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve summary information, clean QQ noise, isolate per-group failures, serialize conflicting operations, and make report replacement recoverable without changing the current product surface.

**Architecture:** Extend the existing preprocessing and summary boundaries rather than replacing the pipeline. Add one focused operation-coordination module, carry explicit per-group outcomes through the pipeline and Web adapter, and keep the current SQLite/report interfaces compatible while strengthening their behavior.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, FastAPI/Jinja, pytest, standard-library filesystem primitives.

---

**Working-tree safety:** The implementation files already contain user-owned, uncommitted changes. Execute the test and diff checkpoints below, but do not stage or commit modified implementation files unless the user first establishes a clean baseline. Commands labelled “checkpoint” replace the usual per-task commit so unrelated hunks are never bundled accidentally.

## File map

- Modify `qq_digest/preprocessing.py`: canonical message cleaning and deterministic extraction.
- Modify `qq_digest/summary.py`: cleaned context construction, resource descriptions, and coverage metrics.
- Modify `qq_digest/reports.py`: lossless Markdown rendering and recoverable paired writes.
- Modify `qq_digest/pipeline.py`: per-group outcomes and failure isolation.
- Modify `qq_digest/archive.py`: accept persisted `partial_success` jobs.
- Modify `qq_digest/scheduler.py`: prove partial runs remain retryable.
- Create `qq_digest/web/operations.py`: central operation conflict coordinator.
- Modify `qq_digest/web/app.py`: use the coordinator and expose partial outcomes.
- Modify `qq_digest/web/templates/base.html`: shared partial-result message formatter.
- Modify `qq_digest/web/templates/dashboard.html`: display partial completion.
- Modify `qq_digest/web/templates/reports.html`: display partial completion.
- Modify `qq_digest/cli.py`: report failed groups without hiding completed work.
- Modify `tests/test_preprocessing.py`, `tests/test_summary.py`, `tests/test_reports.py`, `tests/test_pipeline.py`, `tests/test_scheduler.py`, `tests/test_web.py`, and `tests/test_cli.py`.
- Create `tests/test_operations.py`.

### Task 1: Canonical message cleaning and URL filtering

**Files:**
- Modify: `qq_digest/preprocessing.py:1-39`
- Test: `tests/test_preprocessing.py`

- [ ] **Step 1: Write failing preprocessing tests**

Append tests that define HTML decoding, trailing-punctuation removal, hostname normalization, internal QQ-link filtering, source-index retention, and pure-noise removal:

```python
def test_normalizes_urls_and_keeps_source_indexes():
    result = Preprocessor().process([
        "忽略", "  文档 https://EXAMPLE.com/a?x=1&amp;y=2。  "
    ])

    assert result.cleaned_lines == [
        "忽略", "文档 https://EXAMPLE.com/a?x=1&y=2。"
    ]
    assert result.kept_indexes == [0, 1]
    assert result.links == ["https://example.com/a?x=1&y=2"]


def test_excludes_internal_qq_urls_without_dropping_user_text():
    result = Preprocessor().process([
        "https://tianquan.gtimg.cn/nudgeaction/item/10/expression.jpg",
        "这个入口打不开 https://zb.vip.qq.com/v2/pages/nudgeMall?_wv=2",
    ])

    assert result.links == []
    assert result.cleaned_lines == [
        "这个入口打不开 https://zb.vip.qq.com/v2/pages/nudgeMall?_wv=2"
    ]
    assert result.kept_indexes == [1]
    assert result.discarded_count == 1
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_preprocessing.py -q
```

Expected: FAIL because `kept_indexes` does not exist and URL values are neither decoded nor canonicalized.

- [ ] **Step 3: Implement the cleaning helpers**

Add standard-library imports and helpers, then make `process()` call `clean_line()` exactly once per source line:

```python
import html
from urllib.parse import urlsplit, urlunsplit

TRAILING_URL_PUNCTUATION = ".,!?;:，。！？；：、）)]}》”'"
INTERNAL_QQ_HOSTS = {"tianquan.gtimg.cn", "zb.vip.qq.com"}


def normalize_url(value: str) -> str:
    value = html.unescape(value).rstrip(TRAILING_URL_PUNCTUATION)
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return value
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{userinfo}{hostname}{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))


def is_internal_qq_url(value: str) -> bool:
    return (urlsplit(value).hostname or "").lower() in INTERNAL_QQ_HOSTS


@dataclass
class PreprocessResult:
    cleaned_lines: list[str] = field(default_factory=list)
    kept_indexes: list[int] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    discarded_count: int = 0


class Preprocessor:
    def clean_line(self, raw: str) -> str | None:
        line = re.sub(r"\s+", " ", html.unescape(raw)).strip()
        if not line or NOISE_PATTERN.match(line):
            return None
        urls = [normalize_url(item) for item in URL_PATTERN.findall(line)]
        without_urls = URL_PATTERN.sub("", line).strip(TRAILING_URL_PUNCTUATION + " ")
        if urls and all(is_internal_qq_url(item) for item in urls) and not without_urls:
            return None
        return line

    def process(self, lines: list[str]) -> PreprocessResult:
        result = PreprocessResult()
        for index, raw in enumerate(lines):
            line = self.clean_line(raw)
            if line is None:
                result.discarded_count += 1
                continue
            result.cleaned_lines.append(line)
            result.kept_indexes.append(index)
            result.links.extend(
                url for url in map(normalize_url, URL_PATTERN.findall(line))
                if not is_internal_qq_url(url)
            )
            file_match = FILE_PATTERN.match(line)
            if file_match:
                result.files.append(file_match.group(1).strip())
            if TODO_PATTERN.search(line):
                result.todos.append(line)
        result.links = list(dict.fromkeys(result.links))
        result.files = list(dict.fromkeys(result.files))
        return result
```

If `urlsplit(...).port` raises for a malformed port, catch `ValueError` in `normalize_url()` and return the punctuation-trimmed value instead of rejecting the full message.

- [ ] **Step 4: Run preprocessing tests and verify GREEN**

Run the Task 1 command again. Expected: all tests in `tests/test_preprocessing.py` PASS.

- [ ] **Step 5: Checkpoint Task 1 without staging user changes**

```powershell
git diff --check -- qq_digest/preprocessing.py tests/test_preprocessing.py
git diff --stat -- qq_digest/preprocessing.py tests/test_preprocessing.py
```

Expected: no whitespace errors; only the reviewed Task 1 files appear in the task diff.

### Task 2: Preserve summary detail from model response to Markdown

**Files:**
- Modify: `qq_digest/summary.py:14-171`
- Modify: `qq_digest/reports.py:14-58`
- Modify: `qq_digest/pipeline.py:193-225`
- Modify: `qq_digest/cli.py:330-370`
- Test: `tests/test_summary.py`
- Test: `tests/test_reports.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing schema and Markdown tests**

Add:

```python
def test_resource_description_survives_validation():
    resource = Resource.model_validate({
        "title": "文档", "url": "https://example.com", "description": "部署指南"
    })
    assert resource.description == "部署指南"


def test_render_markdown_preserves_topic_and_resource_descriptions():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-09-09",
        window="00:00 到 22:00",
        topics=[{"topic": "工具推荐", "summary": "适合处理日志"}],
        conclusions=[],
        resources=[{
            "title": "站点", "url": "https://example.com", "description": "官方文档"
        }],
        tasks=[],
        open_questions=[],
        deterministic={"links": [], "files": [], "todos": []},
        quality_note="",
    )
    assert "**工具推荐**：适合处理日志" in markdown
    assert "[站点](https://example.com)：官方文档" in markdown
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_summary.py tests/test_reports.py -q
```

Expected: FAIL because `Resource.description` and mapping-based report rendering are absent.

- [ ] **Step 3: Add the field and lossless renderers**

In `summary.py`:

```python
class Resource(BaseModel):
    title: str
    url: str = Field(default="", validation_alias=AliasChoices("url", "link"))
    description: str = ""
```

In `reports.py`, retain string compatibility while accepting structured mappings:

```python
from collections.abc import Mapping


def _topic_line(item: str | Mapping[str, str]) -> str:
    if isinstance(item, str):
        return item
    title = item.get("topic", "").strip()
    summary = item.get("summary", "").strip()
    return f"**{title}**：{summary}" if summary else title


def _resource_line(item: str | Mapping[str, str]) -> str:
    if isinstance(item, str):
        return item
    title = item.get("title", "").strip()
    url = item.get("url", "").strip()
    description = item.get("description", "").strip()
    label = f"[{title}]({url})" if url else title
    return f"{label}：{description}" if description else label
```

Change the `topics` and `resources` annotations to accept mappings and call these helpers before `bullets()`.

- [ ] **Step 4: Pass structured objects from both execution paths**

In `pipeline.py` and the `summarize` CLI command, replace the lossy list comprehensions with:

```python
topics=[item.model_dump() for item in summary.response.main_topics],
resources=[item.model_dump() for item in summary.response.resources],
```

- [ ] **Step 5: Make model context consume the preprocessed rows**

Extend `ContextWindow`:

```python
@dataclass(frozen=True)
class ContextWindow:
    text: str
    chars: int
    truncated: bool
    source_messages: int
    included_messages: int
    discarded_messages: int
```

Change `build_context_window()` to accept `cleaned_text_by_index: dict[int, str] | None`, skip indexes absent from that mapping, and calculate the number of selected rows after tail truncation:

```python
def build_context_window(
    messages: list[NormalizedMessage],
    max_chars: int,
    cleaned_text_by_index: dict[int, str] | None = None,
) -> ContextWindow:
    lines: list[str] = []
    for index, message in enumerate(messages):
        if cleaned_text_by_index is not None and index not in cleaned_text_by_index:
            continue
        sender = message.sender_qq or "unknown"
        timestamp = message.timestamp.strftime("%H:%M")
        if cleaned_text_by_index is None:
            text = message.text.strip() or message.raw_digest.strip() or message.message_type
        else:
            text = cleaned_text_by_index[index]
        lines.append(f"[{message.msg_id}|{timestamp}|{sender}] {text}")

    context = "\n".join(lines)
    selected = lines
    truncated = len(context) > max_chars
    if truncated:
        selected = []
        total = 0
        for line in reversed(lines):
            line_length = len(line) + (1 if selected else 0)
            if total + line_length > max_chars:
                break
            selected.append(line)
            total += line_length
        selected.reverse()
        context = "\n".join(selected)

    return ContextWindow(
        text=context,
        chars=len(context),
        truncated=truncated,
        source_messages=len(messages),
        included_messages=len(selected),
        discarded_messages=len(messages) - len(lines),
    )
```

In `Summarizer.summarize()` process once and pass the indexed text:

```python
cleaned = self.preprocessor.process([message.text for message in local_messages])
cleaned_by_index = dict(zip(cleaned.kept_indexes, cleaned.cleaned_lines, strict=True))
context_window = build_context_window(
    local_messages,
    self.max_context_chars,
    cleaned_text_by_index=cleaned_by_index,
)
```

Use `cleaned.links/files/todos` for deterministic extraction. Add the three counts to `SummaryResult`, and make the report quality note state:

```python
quality_note = (
    f"原始 {summary.source_messages} 条，纳入 {summary.included_messages} 条，"
    f"清洗丢弃 {summary.discarded_messages} 条。"
    + (f"上下文达到上限，仅保留最近 {summary.context_chars} 字符。" if summary.context_truncated else "")
)
```

- [ ] **Step 6: Add a cleaned-context regression test**

Use the existing `FakeAI`, `valid_response()`, and `message()` helpers:

```python
def test_summarizer_builds_context_from_cleaned_messages():
    timezone = ZoneInfo("Asia/Shanghai")
    ai = FakeAI(valid_response())
    summarizer = Summarizer(ai=ai, max_context_chars=1000)

    result = summarizer.summarize(
        group_id=123,
        group_name="测试群",
        window_start=datetime(2026, 8, 24, 0, tzinfo=timezone),
        window_end=datetime(2026, 8, 24, 22, tzinfo=timezone),
        messages=[
            message(
                "m1", datetime(2026, 8, 24, 9, tzinfo=timezone),
                "https://tianquan.gtimg.cn/nudgeaction/item/10/expression.jpg",
            ),
            message(
                "m2", datetime(2026, 8, 24, 10, tzinfo=timezone),
                "保留这条普通讨论",
            ),
        ],
        timezone=timezone,
    )

    user_prompt = ai.calls[0][1]["content"]
    assert "保留这条普通讨论" in user_prompt
    assert "tianquan.gtimg.cn" not in user_prompt
    assert result.source_messages == 2
    assert result.included_messages == 1
    assert result.discarded_messages == 1
```

- [ ] **Step 7: Run focused summary, report, and pipeline tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_preprocessing.py tests/test_summary.py tests/test_reports.py tests/test_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 8: Checkpoint Task 2 without staging user changes**

```powershell
git diff --check -- qq_digest/summary.py qq_digest/reports.py qq_digest/pipeline.py qq_digest/cli.py tests/test_summary.py tests/test_reports.py tests/test_pipeline.py
git diff --stat -- qq_digest/summary.py qq_digest/reports.py qq_digest/pipeline.py qq_digest/cli.py tests/test_summary.py tests/test_reports.py tests/test_pipeline.py
```

### Task 3: Recoverable paired report writes

**Files:**
- Modify: `qq_digest/reports.py:1-81`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write a failing rollback test**

Create an existing Markdown/JSON pair, monkeypatch the second temporary-file replacement to fail, and assert both old files survive:

```python
def test_report_writer_restores_existing_pair_when_json_replace_fails(tmp_path, monkeypatch):
    writer = ReportWriter(tmp_path)
    original = writer.write(
        group_id=123, group_name="群", report_date="2026-09-09",
        markdown="old markdown", payload={"version": "old"},
    )
    real_replace = os.replace

    def fail_json_install(source, target):
        source_path, target_path = Path(source), Path(target)
        if source_path.suffix == ".tmp" and target_path.suffix == ".json":
            raise OSError("simulated json replace failure")
        return real_replace(source, target)

    monkeypatch.setattr("qq_digest.reports.os.replace", fail_json_install)
    with pytest.raises(OSError, match="simulated"):
        writer.write(
            group_id=123, group_name="群", report_date="2026-09-09",
            markdown="new markdown", payload={"version": "new"},
        )

    assert original.markdown.read_text(encoding="utf-8") == "old markdown"
    assert json.loads(original.json.read_text(encoding="utf-8")) == {"version": "old"}
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))
```

- [ ] **Step 2: Run the rollback test and verify RED**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_reports.py::test_report_writer_restores_existing_pair_when_json_replace_fails -q
```

Expected: FAIL because current `write_text()` has no replace boundary or rollback.

- [ ] **Step 3: Implement temporary preparation and rollback**

Add `os`, `shutil`, `tempfile`, and `uuid4`. Implement:

```python
def _prepare_text(target: Path, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=target.parent,
        prefix=f".{target.name}.", suffix=".tmp", delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _backup_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.{uuid4().hex}.bak")
```

In `ReportWriter.write()`:

```python
markdown_tmp = json_tmp = None
backups: dict[Path, Path] = {}
installed: list[Path] = []
try:
    markdown_tmp = _prepare_text(markdown_path, markdown)
    json_tmp = _prepare_text(
        json_path, json.dumps(payload, ensure_ascii=False, indent=2)
    )
    for target in (markdown_path, json_path):
        if target.exists():
            backup = _backup_path(target)
            shutil.copy2(target, backup)
            backups[target] = backup
    for temporary, target in ((markdown_tmp, markdown_path), (json_tmp, json_path)):
        os.replace(temporary, target)
        installed.append(target)
    markdown_tmp = json_tmp = None
except Exception:
    for target in reversed(installed):
        backup = backups.get(target)
        if backup and backup.exists():
            os.replace(backup, target)
        else:
            target.unlink(missing_ok=True)
    raise
finally:
    for path in (markdown_tmp, json_tmp, *backups.values()):
        if path is not None:
            path.unlink(missing_ok=True)
```

- [ ] **Step 4: Add preparation-failure and first-write cleanup tests**

Patch `_prepare_text` to fail on JSON preparation. Verify an existing pair remains unchanged and a first-time write leaves neither target nor temp/backup artifacts.

- [ ] **Step 5: Run all report tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_reports.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint Task 3 without staging user changes**

```powershell
git diff --check -- qq_digest/reports.py tests/test_reports.py
git diff --stat -- qq_digest/reports.py tests/test_reports.py
```

### Task 4: Isolate per-group failures and persist partial success

**Files:**
- Modify: `qq_digest/pipeline.py:38-283`
- Modify: `qq_digest/archive.py:645-658`
- Modify: `qq_digest/scheduler.py:17-65`
- Modify: `qq_digest/cli.py:470-514`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write a failing multi-group isolation test**

Create two groups and inject a summarizer that fails only for group 123:

```python
from qq_digest.summary import SummaryResponse, SummaryResult


class SelectiveSummarizer:
    def __init__(self, failing_ids):
        self.failing_ids = set(failing_ids)
        self.calls = []

    def summarize(self, **kwargs):
        group_id = kwargs["group_id"]
        self.calls.append(group_id)
        if group_id in self.failing_ids:
            raise RuntimeError("temporary AI outage")
        return SummaryResult(
            response=SummaryResponse(
                group_id=group_id,
                main_topics=[], conclusions=[], resources=[], tasks=[],
                open_questions=[], candidates=[],
            ),
            deterministic={"links": [], "files": [], "todos": []},
            context_chars=10,
            context_truncated=False,
            source_messages=1,
            included_messages=1,
            discarded_messages=0,
        )


def test_run_daily_continues_after_one_group_summary_fails(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([
        GroupConfig(group_id=123, name="群一"),
        GroupConfig(group_id=456, name="群二"),
    ])
    collector = MutableCollector([
        NormalizedMessage(
            msg_id="one", group_id=123, sender_qq=1,
            timestamp=now.replace(hour=9), text="第一群消息", collected_at=now,
        ),
        NormalizedMessage(
            msg_id="two", group_id=456, sender_qq=2,
            timestamp=now.replace(hour=10), text="第二群消息", collected_at=now,
        ),
    ])
    pipeline = DailyPipeline(
        archive=archive, collector=collector, ai_client=FakeAI(),
        report_dir=tmp_path / "reports", max_context_chars=1000,
        timezone_name="Asia/Shanghai",
    )
    pipeline.summarizer = SelectiveSummarizer({123})
    result = pipeline.run_daily(now)

    assert result.status == "partial_success"
    assert [item.group_id for item in result.failed_groups] == [123]
    assert result.succeeded_groups == [456]
    assert archive.report_for(123, "2026-08-24") is None
    assert archive.report_for(456, "2026-08-24") is not None
    assert archive.connection.execute(
        "SELECT status FROM jobs ORDER BY job_id DESC"
    ).fetchone()["status"] == "partial_success"
```

- [ ] **Step 2: Run the isolation test and verify RED**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_pipeline.py::test_run_daily_continues_after_one_group_summary_fails -q
```

Expected: FAIL because the first AI exception currently aborts the pipeline.

- [ ] **Step 3: Add explicit group outcomes**

In `pipeline.py`:

```python
@dataclass(frozen=True)
class GroupRunFailure:
    group_id: int
    group_name: str
    stage: str
    error: str


@dataclass
class DailyRunResult:
    report_date: str
    groups_processed: int
    messages_inserted: int
    report_paths: list[ReportPaths] = field(default_factory=list)
    candidate_ids: list[int] = field(default_factory=list)
    succeeded_groups: list[int] = field(default_factory=list)
    skipped_groups: list[int] = field(default_factory=list)
    failed_groups: list[GroupRunFailure] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial_success" if self.failed_groups else "success"
```

Add a bounded error helper:

```python
def _group_failure(group, stage: str, exc: Exception) -> GroupRunFailure:
    detail = " ".join(str(exc).split())[:200]
    return GroupRunFailure(
        group_id=group.group_id,
        group_name=group.name,
        stage=stage,
        error=f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__,
    )
```

Record collection failures as `stage="collection"`. Wrap each prepared-group summary/write/candidate/record block in `try/except Exception`, append a `stage="summary"` failure, log with `exc_info=True`, and continue. Append IDs to `succeeded_groups` only after the report record and notifications complete; append empty/fingerprint-matched groups to `skipped_groups`.

- [ ] **Step 4: Persist the aggregate job status**

Change `run_daily()` to finish with the result status:

```python
result = self._run_without_job_tracking(now)
self.archive.finish_job(
    job_id,
    result.status,
    json.dumps([asdict(item) for item in result.failed_groups], ensure_ascii=False)
    if result.failed_groups else "",
)
return result
```

Import `json` and `asdict`. Extend `Archive.finish_job()` validation to:

```python
if status not in {"success", "partial_success", "failed"}:
    raise ValueError("job status 只支持 success、partial_success 或 failed")
```

- [ ] **Step 5: Prove a retry skips previous successes**

Extend the Task 4 fixture into a second test:

```python
def test_partial_retry_only_calls_groups_without_current_report(tmp_path):
    pipeline, archive, now = make_two_group_pipeline(tmp_path)
    selective = SelectiveSummarizer({123})
    pipeline.summarizer = selective

    first = pipeline.run_daily(now)
    assert first.status == "partial_success"
    selective.calls.clear()
    selective.failing_ids.clear()

    second = pipeline.run_daily(now)

    assert second.status == "success"
    assert selective.calls == [123]
    assert archive.report_for(123, "2026-08-24") is not None
    assert archive.report_for(456, "2026-08-24") is not None
    statuses = [row["status"] for row in archive.connection.execute(
        "SELECT status FROM jobs ORDER BY job_id"
    )]
    assert statuses == ["partial_success", "success"]
```

Extract the complete arrangement from the previous test into `make_two_group_pipeline(tmp_path)` returning `(pipeline, archive, now)`; both tests must call real `DailyPipeline.run_daily()`.

- [ ] **Step 6: Prove partial jobs remain retryable**

Add a `partial_success` job row to `tests/test_scheduler.py`:

```python
def test_daily_retry_retries_partial_success_after_interval():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY, job_type TEXT, status TEXT, "
        "started_at TEXT, finished_at TEXT, error TEXT)"
    )
    connection.execute(
        "INSERT INTO jobs VALUES "
        "(1,'daily_digest','partial_success','2026-08-24T14:00:00+00:00',"
        "'2026-08-24T14:01:00+00:00','group 123')"
    )
    timezone = ZoneInfo("Asia/Shanghai")

    waiting = daily_retry_state(
        connection, datetime(2026, 8, 24, 22, 10, tzinfo=timezone),
        target_hour=22, target_minute=0, max_attempts=3,
        retry_interval_minutes=15,
    )
    due = daily_retry_state(
        connection, datetime(2026, 8, 24, 22, 17, tzinfo=timezone),
        target_hour=22, target_minute=0, max_attempts=3,
        retry_interval_minutes=15,
    )

    assert waiting.due is False
    assert due.due is True
    assert due.succeeded is False
```

- [ ] **Step 7: Update CLI result text**

After the existing run summary, emit one bounded line for failures:

```python
if result.failed_groups:
    typer.echo(
        "部分群处理失败: "
        + "; ".join(f"{item.group_name}({item.stage})" for item in result.failed_groups),
        err=True,
    )
```

Add a CLI test that invokes the command with a patched pipeline result and asserts completed counts remain on stdout and failed group names appear on stderr.

- [ ] **Step 8: Run pipeline, scheduler, archive, and CLI tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_scheduler.py tests/test_archive.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 9: Checkpoint Task 4 without staging user changes**

```powershell
git diff --check -- qq_digest/pipeline.py qq_digest/archive.py qq_digest/scheduler.py qq_digest/cli.py tests/test_pipeline.py tests/test_scheduler.py tests/test_archive.py tests/test_cli.py
git diff --stat -- qq_digest/pipeline.py qq_digest/archive.py qq_digest/scheduler.py qq_digest/cli.py tests/test_pipeline.py tests/test_scheduler.py tests/test_archive.py tests/test_cli.py
```

### Task 5: Central operation coordinator

**Files:**
- Create: `qq_digest/web/operations.py`
- Create: `tests/test_operations.py`
- Modify: `qq_digest/web/app.py:56-391,660-779,867-894`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing coordinator unit tests**

```python
import pytest

from qq_digest.web.operations import OperationBusy, OperationCoordinator


@pytest.mark.parametrize("first,second", [
    ("refresh", "collect"), ("collect", "daily"),
    ("sync", "refresh"), ("daily", "sync"),
])
def test_conflicting_operations_are_rejected(first, second):
    operations = OperationCoordinator()
    with operations.claim(first):
        with pytest.raises(OperationBusy) as caught:
            with operations.claim(second):
                pass
        assert caught.value.active == (first,)


def test_claim_releases_state_after_exception():
    operations = OperationCoordinator()
    with pytest.raises(RuntimeError):
        with operations.claim("daily"):
            raise RuntimeError("boom")
    assert operations.snapshot() == {
        "daily_running": False, "refresh_running": False,
        "collect_running": False, "sync_running": False,
    }
```

- [ ] **Step 2: Run coordinator tests and verify RED**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_operations.py -q
```

Expected: collection error because the module does not exist.

- [ ] **Step 3: Implement the coordinator**

```python
from contextlib import contextmanager
from threading import Lock

OPERATION_KINDS = ("daily", "refresh", "collect", "sync")
CONFLICTS = {kind: frozenset(OPERATION_KINDS) for kind in OPERATION_KINDS}


class OperationBusy(RuntimeError):
    def __init__(self, requested: str, active: tuple[str, ...]):
        super().__init__(f"操作 {requested} 与正在运行的 {', '.join(active)} 冲突")
        self.requested = requested
        self.active = active


class OperationCoordinator:
    def __init__(self):
        self._active: set[str] = set()
        self._lock = Lock()

    @contextmanager
    def claim(self, kind: str):
        if kind not in CONFLICTS:
            raise ValueError(f"未知操作: {kind}")
        with self._lock:
            active = tuple(sorted(self._active & CONFLICTS[kind]))
            if active:
                raise OperationBusy(kind, active)
            self._active.add(kind)
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(kind)

    def can_start(self, kind: str) -> bool:
        with self._lock:
            return not bool(self._active & CONFLICTS[kind])

    def is_active(self, kind: str) -> bool:
        with self._lock:
            return kind in self._active

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return {f"{kind}_running": kind in self._active for kind in OPERATION_KINDS}
```

- [ ] **Step 4: Run coordinator tests and verify GREEN**

Run the Task 5 focused command again. Expected: PASS.

- [ ] **Step 5: Integrate the coordinator into `create_app()`**

Instantiate once and expose it:

```python
operations = OperationCoordinator()
app.state.operations = operations
```

Replace every direct `operation_state` mutation with a claim surrounding the complete operation:

```python
try:
    with operations.claim("collect"):
        msgs, ingest = await asyncio.to_thread(collect_in_worker)
except OperationBusy as exc:
    raise HTTPException(
        status_code=409,
        detail={"message": "已有冲突任务在运行", "active": exc.active},
    ) from exc
```

Apply the same pattern to manual `refresh` and `daily`. Background loops use `can_start()` to avoid starting work and still claim immediately before `to_thread()`. Remove the old mutable dictionary; scheduler status reads `operations.is_active("daily")` and `operations.is_active("sync")`.

- [ ] **Step 6: Add Web conflict tests**

Replace direct mutation of `app.state.operation_state` with:

```python
with client.app.state.operations.claim("sync"):
    response = client.post("/api/run-daily")
assert response.status_code == 409
assert response.json()["detail"]["active"] == ["sync"]
```

Add equivalent tests proving collect is rejected during refresh and refresh is rejected during collect.

- [ ] **Step 7: Run coordinator and Web tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_operations.py tests/test_web.py -q
```

Expected: PASS.

- [ ] **Step 8: Checkpoint Task 5 without staging user changes**

```powershell
git diff --check -- qq_digest/web/operations.py qq_digest/web/app.py tests/test_operations.py tests/test_web.py
git diff --stat -- qq_digest/web/operations.py qq_digest/web/app.py tests/test_operations.py tests/test_web.py
```

### Task 6: Expose partial completion through Web and scheduler state

**Files:**
- Modify: `qq_digest/web/app.py:88-275,721-742,867-894`
- Modify: `qq_digest/web/templates/base.html:230-260`
- Modify: `qq_digest/web/templates/dashboard.html:37-110`
- Modify: `qq_digest/web/templates/reports.html:32-65`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write failing Web adapter tests**

Add a failing AI provider through the existing factory seam and assert `/api/run-daily` returns a structured partial result rather than HTTP 500:

```python
from types import SimpleNamespace


class FailingWebAI:
    def chat(self, messages):
        raise RuntimeError("temporary AI outage")

    def close(self):
        pass


def test_run_daily_returns_partial_group_details(web_client, monkeypatch):
    client, _, _ = web_client
    monkeypatch.setattr(
        "qq_digest.ai.factory.build_ai_client", lambda config: FailingWebAI()
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/run-daily")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial_success"
    assert data["reports"] == 0
    assert data["succeeded_groups"] == []
    assert data["failed_groups"][0]["group_id"] == 123
    assert data["failed_groups"][0]["stage"] == "summary"
    assert "temporary AI outage" in data["failed_groups"][0]["error"]
```

Also prove a preflight refresh failure still returns HTTP 500:

```python
def test_run_daily_preflight_failure_remains_server_error(web_client, monkeypatch, tmp_path):
    client, _, _ = web_client
    cfg = client.app.state.config
    cfg.ntqq.enabled = True
    cfg.ntqq.db_dir = tmp_path / "decrypted"
    monkeypatch.setattr(
        "qq_digest.refresh.refresh_database",
        lambda **kwargs: SimpleNamespace(success=False, message="snapshot failed"),
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/run-daily")

    assert response.status_code == 500
    assert "snapshot failed" in response.json()["detail"]
```

- [ ] **Step 2: Run the new Web tests and verify RED**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -k "partial or preflight" -q
```

Expected: FAIL because `_run_daily_task` currently hardcodes `success=True` and the endpoint converts every non-success into 500.

- [ ] **Step 3: Serialize full daily results**

Return:

```python
return {
    "success": result.status == "success",
    "status": result.status,
    "groups_processed": result.groups_processed,
    "messages_inserted": result.messages_inserted,
    "reports": len(result.report_paths),
    "candidates": len(result.candidate_ids),
    "succeeded_groups": result.succeeded_groups,
    "skipped_groups": result.skipped_groups,
    "failed_groups": [asdict(item) for item in result.failed_groups],
}
```

In `/api/run-daily`, raise HTTP 500 only when `status == "failed"`; return partial results normally. Set `last_run_date` only for full success, while keeping `last_result` for dashboard visibility.

- [ ] **Step 4: Add one shared formatter and update both run buttons**

Add the formatter beside `formatNumber()` in `base.html`:

```javascript
function dailyResultMessage(data) {
  if (data.status === 'partial_success') {
    return '已生成 ' + formatNumber(data.reports) + ' 份摘要，'
      + formatNumber(data.failed_groups.length) + ' 个群失败并将重试';
  }
  return '处理 ' + formatNumber(data.groups_processed) + ' 个群，'
    + formatNumber(data.messages_inserted) + ' 条消息';
}
```

In both `dashboard.html` and `reports.html`, replace the existing success text with:

```javascript
toast(
  dailyResultMessage(data),
  data.status === 'partial_success' ? 'error' : 'success'
);
```

Do not render raw failure strings into HTML.

- [ ] **Step 5: Run Web tests**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint Task 6 without staging user changes**

```powershell
git diff --check -- qq_digest/web/app.py qq_digest/web/templates/base.html qq_digest/web/templates/dashboard.html qq_digest/web/templates/reports.html tests/test_web.py
git diff --stat -- qq_digest/web/app.py qq_digest/web/templates/base.html qq_digest/web/templates/dashboard.html qq_digest/web/templates/reports.html tests/test_web.py
```

### Task 7: Full verification and browser smoke test

**Files:**
- Modify only if verification reveals a regression, with a new failing test first.

- [ ] **Step 1: Run the entire test suite**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest
```

Expected: all tests PASS with no new warnings. The existing third-party Starlette deprecation warning may remain documented if it is unchanged.

- [ ] **Step 2: Check whitespace and accidental edits**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only planned files and pre-existing user changes are present.

- [ ] **Step 3: Start the local server with isolated test data**

Use the existing test configuration or a temporary configuration rooted under the project `work/qa-p0/`; do not use or overwrite the live `archive/archive.sqlite` or live reports. Start via the configured Codex Python environment.

- [ ] **Step 4: Run real-browser desktop and mobile smoke checks**

Verify:

- login and navigation still work;
- dashboard and reports pages load without console errors;
- the run button disables while active;
- a simulated partial result produces the bounded partial-success toast;
- desktop layout at approximately 1440×900 has no overlap;
- mobile layout at approximately 390×844 keeps the button and result text visible.

Save screenshots to `output/playwright/p0-hardening-dashboard-desktop.png` and `output/playwright/p0-hardening-reports-mobile.png`.

- [ ] **Step 5: Run a fresh final suite after any QA fixes**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest
```

Expected: all tests PASS.

- [ ] **Step 6: Request code review and resolve findings**

Review the implementation against `docs/superpowers/specs/2026-09-09-qq-digest-p0-hardening-design.md`. Fix every critical or important finding with a failing regression test first, then rerun the full suite.

- [ ] **Step 7: Inspect any QA changes without staging user work**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors. Do not stage or commit unrelated pre-existing working-tree files.

## Final verification checklist

- [ ] Every new production function has a focused test.
- [ ] Each new behavior was observed failing before implementation.
- [ ] Topic summaries and resource descriptions appear in Markdown.
- [ ] QQ internal links are absent from deterministic resources.
- [ ] Cleaned rows, not raw rows, form the AI context.
- [ ] One failed group does not block later groups.
- [ ] Partial jobs remain retryable and successful groups remain idempotent.
- [ ] Every conflicting Web/background operation goes through `OperationCoordinator`.
- [ ] Report write failures preserve the previous Markdown/JSON pair.
- [ ] Full pytest and browser smoke checks pass.
