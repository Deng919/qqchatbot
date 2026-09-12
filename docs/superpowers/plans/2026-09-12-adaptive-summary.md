# Adaptive Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace user-selectable summary templates with one adaptive summary flow that produces richer, evidence-bound QQ group digests.

**Architecture:** Keep the existing structured JSON, candidate extraction, archive tables, and report publication transaction. Remove template choice from the active domain and UI, add a backward-compatible `overview` field, and use one category-aware adaptive prompt for both daily and range summaries. Legacy database/config fields remain readable but are ignored; newly written legacy markers use the constant `adaptive` so no destructive SQLite migration is required.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, SQLite, Jinja2/vanilla JavaScript, pytest, Codex in-app browser.

---

## File map

- `qq_digest/prompt_builder.py`: owns the single adaptive output contract and category strategies.
- `qq_digest/summary.py`: validates `overview` and calls prompt builders without a template.
- `qq_digest/reports.py`: renders the new “今日概览” section.
- `qq_digest/group_summary.py`: passes adaptive summary data into report rendering and fingerprints.
- `qq_digest/pipeline.py`: stops reading a group template during daily generation.
- `qq_digest/manual_summary.py`: removes public detail selection and uses one adaptive archive key.
- `qq_digest/web/app.py`: hides legacy template fields from APIs and ignores legacy range payload values.
- `qq_digest/web/templates/groups.html`: removes the group template column and editor.
- `qq_digest/web/templates/reports.html`: removes template columns, labels, and range-detail controls.
- `qq_digest/web/templates/dashboard.html`: removes template from group statistics.
- `docs/ai/prompt-template.md`, `docs/ai/summary-guidelines.md`, `config/config.example.yaml`, `README.md`: document adaptive behavior and remove template instructions.
- `tests/test_summary.py`, `tests/test_reports.py`, `tests/test_group_summary.py`, `tests/test_pipeline.py`, `tests/test_manual_summary.py`, `tests/test_web.py`, `tests/test_config.py`: regression coverage.

### Task 1: Define the adaptive prompt and response contract

**Files:**
- Modify: `tests/test_summary.py`
- Modify: `qq_digest/prompt_builder.py`
- Modify: `qq_digest/summary.py`

- [ ] **Step 1: Write failing prompt and response tests**

Add tests that require a defaultable overview and prove that no template marker reaches the model:

```python
from qq_digest.prompt_builder import build_system_prompt
from qq_digest.summary import SummaryResponse


def test_adaptive_prompt_requests_rich_evidence_bound_topics():
    prompt = build_system_prompt("general")
    assert "今日概览" in prompt
    assert "通常 3 至 8 个" in prompt
    assert "背景、主要观点、结论或当前状态" in prompt
    assert "两条及以上有信息量的往来" in prompt
    assert "为了凑数" in prompt
    assert "不超过 30 字" not in prompt


def test_summary_response_overview_is_backward_compatible():
    payload = valid_response()
    assert SummaryResponse.model_validate(payload).overview == ""
    payload["overview"] = "当天围绕模型额度与客户端兼容性展开讨论。"
    assert SummaryResponse.model_validate(payload).overview.startswith("当天")


def test_summarizer_does_not_send_template_to_model():
    ai = FakeAI({**valid_response(), "overview": "概览"})
    summarizer = Summarizer(ai=ai, max_context_chars=1000)
    summarizer.summarize(
        group_id=123,
        group_name="测试群",
        category="general",
        window_start=datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        window_end=datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        messages=[message(
            "m1",
            datetime(2026, 8, 24, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
            "有效消息",
        )],
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    combined = "\n".join(item["content"] for item in ai.calls[0])
    assert "模板:" not in combined
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_summary.py -q
```

Expected: failures for the old two-argument `build_system_prompt`, missing `overview`, or missing adaptive instructions.

- [ ] **Step 3: Replace the template variants with one adaptive contract**

In `qq_digest/prompt_builder.py`, replace `FORMAT_CONCISE`, `FORMAT_DETAILED`, `_FORMAT_MAP`, and template-dependent builder arguments with:

```python
FORMAT_ADAPTIVE = """\
输出 JSON，字段如下：
- group_id: 整数，群的 group_id
- overview: 字符串，用 2 至 4 句概括当天讨论重心、主要进展和整体状态
- main_topics: 数组，内容充分时通常 3 至 8 个；每条含 topic 和 summary
- conclusions: 数组，每条是讨论后明确形成的决定、判断或验证结果
- resources: 数组，每条含 title、url、description
- tasks: 数组，每条含 owner、description、deadline（可选）
- open_questions: 数组，每条是仍待确认的问题或争议
- candidates: 数组，每条含 title、reason、type、content、link、message_ids

main_topics 的 summary 应覆盖背景、主要观点、结论或当前状态；信息充分时约 80 至 150 字，信息不足时按实际内容缩短。参与者归属明确时可以注明，不得猜测。"""

ADAPTIVE_COVERAGE = """\
自适应覆盖规则：
- 两条及以上有信息量的往来可以形成话题，不再统一要求五条消息。
- 单条完整公告或重要事件可以形成话题；单条资源、任务或问题优先进入对应字段。
- 相近消息合并，同一事实不要在多个章节机械重复。
- 纯闲聊、表情回应、无上下文片段和系统通知不能用于扩充篇幅。
- 内容不足时允许减少话题或返回空数组，不得为了凑数编造内容。"""


def build_system_prompt(category: str) -> str:
    return "\n\n".join([
        ROLE_PROMPT,
        FORMAT_ADAPTIVE,
        _STRATEGY_MAP.get(category, STRATEGY_GENERAL),
        ADAPTIVE_COVERAGE,
        _CANDIDATE_MAP.get(category, CANDIDATE_GENERAL) + "\n\n" + CANDIDATE_COMMON,
        CONSTRAINTS_PROMPT,
    ])
```

Remove the five-message rule from `STRATEGY_GENERAL` and `CONSTRAINTS_PROMPT`. Remove `template` from `build_user_prompt(...)` and its emitted metadata.

In `qq_digest/summary.py`, add a backward-compatible field and remove the template argument from `Summarizer.summarize(...)`:

```python
class SummaryResponse(BaseModel):
    group_id: int
    overview: str = ""
    main_topics: list[Topic]
    conclusions: list[str]
    resources: list[Resource]
    tasks: list[TaskOutput]
    open_questions: list[str]
    candidates: list[CandidateOutput]

# inside summarize
system_prompt = build_system_prompt(category)
user_prompt = build_user_prompt(
    group_id=group_id,
    group_name=group_name,
    category=category,
    report_date=report_date,
    window_start=window_start.isoformat(),
    window_end=window_end.isoformat(),
    message_count=len(messages),
    context=context_window.text,
    deterministic=deterministic,
    keywords=keywords,
    knowledge_base=knowledge_base,
    context_truncated=context_window.truncated,
)
```

- [ ] **Step 4: Run the focused tests and confirm pass**

Run the same pytest command. Expected: all `tests/test_summary.py` tests pass.

- [ ] **Step 5: Commit only if the touched files contain no unrelated pre-existing edits**

Run `git diff -- qq_digest/prompt_builder.py qq_digest/summary.py tests/test_summary.py` and inspect it first. If safe:

```powershell
git add -- qq_digest/prompt_builder.py qq_digest/summary.py tests/test_summary.py
git commit -m "feat: add adaptive summary prompt"
```

If unrelated edits are present, leave the files uncommitted and record the passing test checkpoint.

### Task 2: Render the overview through the shared summary builder

**Files:**
- Modify: `tests/test_reports.py`
- Modify: `tests/test_group_summary.py`
- Modify: `qq_digest/reports.py`
- Modify: `qq_digest/group_summary.py`

- [ ] **Step 1: Write failing rendering and builder tests**

Add `overview="今天集中讨论客户端兼容性。"` to the report fixture and assert:

```python
assert "## 今日概览\n今天集中讨论客户端兼容性。" in markdown
```

In the group builder test, return `overview="群内确认了工具回退方案。"` from the stub response and assert the generated Markdown contains it. Also assert `"template" not in summarizer.last_kwargs` after the stub stores received keyword arguments.

- [ ] **Step 2: Run tests and confirm failure**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_reports.py tests/test_group_summary.py -q
```

Expected: `render_markdown` rejects `overview` or the new section is absent.

- [ ] **Step 3: Add overview rendering and remove template flow**

Change `render_markdown` by adding `overview: str = ""` after the required `quality_note` parameter and insert this block before “主要话题”:

```python
lines = [
    f"# {group_name}{title_suffix}",
    "",
    f"- {date_label}：{report_date}",
    f"- 时间窗：{window}",
]
if overview.strip():
    lines.extend(["", "## 今日概览", overview.strip()])
lines.extend([
    "",
    "## 主要话题",
    *bullets([topic_line(item) for item in topics]),
])
```

Change `GroupSummaryBuilder.build(...)` to remove `template`, pass no template to the summarizer, and call:

```python
markdown = render_markdown(
    group_name=group.name,
    report_date=report_date,
    window=f"{window_start.isoformat()} 到 {window_end.isoformat()}",
    overview=summary.response.overview,
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
```

Change `summary_input_fingerprint(...)` to remove `template` and use `build_system_prompt(group.category)`. Bump its version to `3` so reports generated with the old prompt are not reused.

- [ ] **Step 4: Run tests and confirm pass**

Run the Task 2 pytest command. Expected: all selected tests pass.

- [ ] **Step 5: Create a safe checkpoint**

Inspect the four-file diff. Commit only when it contains no unrelated prior work; otherwise leave it unstaged.

### Task 3: Make daily and range generation template-free

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_manual_summary.py`
- Modify: `qq_digest/pipeline.py`
- Modify: `qq_digest/manual_summary.py`

- [ ] **Step 1: Replace template-specific tests with adaptive behavior tests**

For daily generation, assert the summarizer input and output payload no longer contain `template`. For range generation, construct requests without detail mode:

```python
request = ManualSummaryRequest(
    group_ids=(123, 456),
    start_date=date(2026, 9, 1),
    end_date=date(2026, 9, 7),
)
first = service.run(request)
second = service.run(request)
assert [item.group_id for item in first.created_reports] == [123, 456]
assert second.created_reports == []
assert [item.group_id for item in second.reused_reports] == [123, 456]
assert all("模板:" not in prompt for prompt in ai.prompts)
```

Remove validation cases and assertions that depend on `detail_mode` or `effective_template`.

- [ ] **Step 2: Run tests and confirm failure**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_manual_summary.py -q
```

Expected: failures from template arguments still passed by the services.

- [ ] **Step 3: Update daily generation**

In `qq_digest/pipeline.py`, call `summary_input_fingerprint(...)` and `GroupSummaryBuilder.build(...)` without `template`. Continue writing the legacy `effective_template` archive column as the fixed string `"adaptive"`; it is not returned by the new API.

- [ ] **Step 4: Update range generation**

In `qq_digest/manual_summary.py`:

```python
@dataclass(frozen=True)
class ManualSummaryRequest:
    group_ids: Sequence[int]
    start_date: date
    end_date: date

@dataclass(frozen=True)
class ManualReportResult:
    report_id: int
    report_key: str
    group_id: int
    group_name: str
    start_date: str
    end_date: str
```

Use `archive_mode = "adaptive"` for `manual_report_for(...)`, report stems, and legacy archive writes. Reuse requires a matching input fingerprint and valid files; do not compare a group template. Omit `detail_mode` and `effective_template` from the JSON payload generated for new reports.

- [ ] **Step 5: Run tests and confirm pass**

Run the Task 3 pytest command. Expected: all selected tests pass.

- [ ] **Step 6: Create a safe checkpoint**

Inspect the four-file diff and commit only if it does not absorb unrelated pre-existing work.

### Task 4: Remove template controls from APIs and pages

**Files:**
- Modify: `tests/test_web.py`
- Modify: `qq_digest/web/app.py`
- Modify: `qq_digest/web/templates/groups.html`
- Modify: `qq_digest/web/templates/reports.html`
- Modify: `qq_digest/web/templates/dashboard.html`

- [ ] **Step 1: Write failing web contract tests**

Add assertions:

```python
groups = client.get("/api/groups").json()["groups"]
assert "template" not in groups[0]

reports = client.get("/api/reports").json()["reports"]
assert all("effective_template" not in report for report in reports)

range_response = client.post(
    "/api/reports/range",
    json={"group_ids": [123], "start_date": "2026-09-01", "end_date": "2026-09-01"},
)
assert range_response.status_code == 200

assert "摘要模板" not in client.get("/groups").text
assert "摘要详细程度" not in client.get("/reports").text
```

Also send legacy `detail_mode="detailed"` once and assert the request remains accepted but produces the same adaptive request, preserving compatibility without exposing the setting.

- [ ] **Step 2: Run the focused web tests and confirm failure**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q
```

Expected: API objects and HTML still expose template fields.

- [ ] **Step 3: Simplify the web API**

Keep `ManualRangePayload.detail_mode: str | None = None` only as an ignored compatibility input. Construct `ManualSummaryRequest` with group IDs and dates only. Remove `template` from `/api/groups`, `/api/groups/stats`, and the PATCH allowlist. Remove `effective_template` from report list and detail responses. Simplify daily report SQL so it no longer selects `g.template`.

- [ ] **Step 4: Simplify the pages**

In `groups.html`, remove the template header, option map, selector, and adjust loading/empty `colspan` from 12 to 11. In `reports.html`, remove the template header/cell and range detail selector, change `colspan` from 7 to 6, and submit only group IDs and dates. In `dashboard.html`, remove the template column and cell from group statistics.

- [ ] **Step 5: Run the focused web tests and confirm pass**

Run the Task 4 pytest command. Expected: all web tests pass.

- [ ] **Step 6: Create a safe checkpoint**

Inspect the five-file diff and commit only if it does not absorb unrelated pre-existing work.

### Task 5: Align config and AI documentation

**Files:**
- Modify: `tests/test_config.py`
- Modify: `config/config.example.yaml`
- Modify: `docs/ai/prompt-template.md`
- Modify: `docs/ai/summary-guidelines.md`
- Modify: `README.md`

- [ ] **Step 1: Add a legacy-config compatibility test**

Load a config containing `template: concise` and assert loading succeeds while summary behavior no longer reads that field. Keep the compatibility field in `GroupConfig` for now because the SQLite adapter still reconstructs existing rows; mark it as deprecated in code rather than making a destructive schema migration.

- [ ] **Step 2: Update user-facing configuration and docs**

Remove `template:` lines from `config.example.yaml` and README configuration examples. Rewrite the AI docs around one adaptive format, including the overview, 3–8 topic target, two-message conversation threshold, single-message exceptions, and the explicit no-padding rule. State that category controls emphasis while message density controls length.

- [ ] **Step 3: Run documentation consistency checks**

```powershell
rg -n "摘要模板|摘要详细程度|template: concise|template: detailed|不超过 30 字|至少 5 条相关消息" README.md config docs/ai qq_digest/web/templates
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: `rg` finds no active user-facing template instructions; the config test passes.

- [ ] **Step 4: Create a safe checkpoint**

Inspect the docs/config diff and commit only if it does not absorb unrelated pre-existing work.

### Task 6: Full regression, live regeneration, and browser QA

**Files:**
- Modify only if a test or verified live defect requires a scoped correction.

- [ ] **Step 1: Run the complete test suite**

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Restart the local service using its existing launch method**

Identify the current process command line, stop only the PID listening on `127.0.0.1:8765`, and restart that same application command. Do not terminate unrelated Python processes.

- [ ] **Step 3: Regenerate today’s reports**

Use the authenticated local UI “生成今日日报” action. Wait for the operation to finish, then verify that all enabled groups either produced or explicitly skipped/failed with a recorded reason. Confirm newly generated JSON contains `overview` and does not contain `template` or `effective_template`.

- [ ] **Step 4: Inspect content quality**

For the five enabled groups, record message count, topic count, overview presence, and report character count. Manually check that substantive conversations are covered, repeated facts are merged, participant attribution is grounded, and idle groups are not padded.

- [ ] **Step 5: Perform desktop and mobile browser QA**

At desktop width, verify group management, range generation, report list, and report detail contain no template controls or columns. At mobile width, verify all remaining controls are reachable, report details show “今日概览,” and there are no console errors or horizontal scroll traps that hide the report action.

- [ ] **Step 6: Report the result**

Provide the exact test count, live job result, generated report count, summary-size comparison, and links to the main changed files. Mention any group that remains intentionally short because its source messages were non-substantive.
