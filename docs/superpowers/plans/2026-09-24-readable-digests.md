# Readable QQ Digests Implementation Plan

> **For agentic workers:** Execute inline in this session. Steps use checkbox syntax for tracking.

**Goal:** Make generated QQ summaries concise, evidence-aware, and free of empty report sections.

**Architecture:** Keep the DeepSeek client and `SummaryResponse` JSON fields unchanged. Adjust prompt constants, deterministic Markdown rendering, and preprocessing separately; store diagnostics in the report JSON rather than reader-facing Markdown.

**Tech Stack:** Python 3, Pydantic, pytest, Markdown text, YAML configuration.

---

### Task 1: Writing and evidence rules

**Files:** `tests/test_summary.py`, `qq_digest/prompt_builder.py`, `docs/ai/prompt-template.md`, `docs/ai/summary-guidelines.md`

- [x] Add tests asserting the generated prompt asks for short direct overview, no topic/word quota, omission of transient chatter, non-repetition, and source-qualified external claims.
- [x] Run `D:\CodexTools\python\Scripts\python.exe -m pytest -q tests/test_summary.py::test_adaptive_prompt_prioritizes_readability_over_quotas` and verify failure against the current quota language.
- [x] Replace the quota and stiff style constraints in `FORMAT_ADAPTIVE`, `ADAPTIVE_COVERAGE`, and `CONSTRAINTS_PROMPT`; preserve the output field names and category-specific candidate limits.
- [x] Update the two AI rule documents to match runtime constants, then rerun `tests/test_summary.py`.

Expected prompt excerpt:

```text
overview 直接说明当天最值得知道的事，通常 1 至 2 句。
main_topics 不设最低数量；临时邀约、短期推广和重复感叹不独立成话题。
外部事实没有原文或可靠链接时标为“群友称”或“尚待核实”，不得写“已确认”。
```

### Task 2: Reader-facing report and diagnostics

**Files:** `tests/test_reports.py`, `tests/test_group_summary.py`, `qq_digest/reports.py`, `qq_digest/group_summary.py`

- [x] Add failing renderer tests: empty arrays create no headings or `- 无`; nonempty sections render; no raw extraction or routine data-quality section appears in Markdown.
- [x] Add a failing builder test asserting `artifact.payload["diagnostics"]` contains message counts, truncation status, and extracted links/files/todos; truncated input still produces a short visible caveat.
- [x] Implement conditional section append in `render_markdown`; keep its public signature compatible with existing callers.
- [x] Add diagnostics to the JSON payload in `GroupSummaryBuilder.build`, and pass a visible note only when input was truncated.
- [x] Rerun both focused test files and update old assertions that expected raw diagnostics in Markdown.

Expected report shape:

```text
# 群名日报
- 日期：...
- 时间窗：...

## 今日概览
...

## 主要话题
...
```

### Task 3: Todo false positives

**Files:** `tests/test_preprocessing.py`, `qq_digest/preprocessing.py`

- [x] Add a failing test: `我记得服务器套餐是半年` does not enter `todos`, while `记得明天提交报告` and `TODO: 修复配置` do.
- [x] Restrict `TODO_PATTERN` to explicit action wording and rerun `tests/test_preprocessing.py`.

### Task 4: Rebuild and verify

**Files:** Five ignored report pairs in `reports/`, local comparison copy in `output/review/2026-09-24-before/`.

- [x] Run all tests with `D:\CodexTools\python\Scripts\python.exe -m pytest -q`, compile with `-m compileall -q qq_digest`, and check `git diff --check`.
- [x] Copy exactly the five existing 2026-09-24 report pairs to `output/review/2026-09-24-before/` after verifying each source path.
- [x] Run `D:\CodexTools\python\Scripts\qq-digest.exe run-daily --config-path config/config.yaml`; the changed prompt fingerprint regenerated five reports.
- [x] Inspect new Markdown/JSON for readability, hidden empty sections, complete diagnostics, and no incorrect “我记得” todo; compare with the copies.
- [ ] Restart the local web service if necessary, verify `/reports` responds, commit only intended tracked files, push `HEAD:main`, and verify remote SHA.

### Review-driven follow-up

- [x] Preserve a distinct one-topic overview; omit it only when its text is contained in the topic. Add regression tests for distinct and blank-topic cases.
- [x] Add a report-format fingerprint version so renderer-only changes regenerate cached reports.
- [x] Correct the truncated-input caveat so it does not claim the included messages are necessarily the newest.
- [x] Accept only DeepSeek's observed single-key wrappers for `conclusions` and `open_questions`, while rejecting ambiguous objects; add tests and clarify the JSON prompt.
