# DeepSeek Settings and Report Q&A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user save a DeepSeek API Key in the local UI and ask evidence-backed, multi-turn questions about a report's archived chat.

**Architecture:** A small key-store module owns the local secret file and overrides older key sources. A report-Q&A service resolves the report's group/window on the server, selects bounded original-message context, calls the existing JSON-speaking DeepSeek client, and validates returned citations. FastAPI exposes narrow authenticated endpoints; Jinja/vanilla JS adds a settings page and an ephemeral report dialog.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, httpx, Jinja, vanilla JavaScript, pytest. Use `D:\CodexTools\python\Scripts\python.exe` for every Python command.

---

## File map

- `qq_digest/ai/key_store.py`: Validate, atomically save and read the UI Key; enforce Windows ACL on the dedicated secret directory.
- `qq_digest/config.py`: Add configurable UI key-file path and resolve it before existing env/file/discovery sources.
- `qq_digest/report_qa.py`: Fetch only the chosen report's archived messages, bound/rank context, call AI, validate citations.
- `qq_digest/web/app.py`: Authenticated settings and report-Q&A endpoints; existing route style remains intact.
- `qq_digest/web/templates/ai_settings.html`: Masked Key setting and explicit connection test.
- `qq_digest/web/templates/reports.html`, `base.html`: Ephemeral, accessible dialog and navigation entry.
- `tests/test_ai_settings.py`, `tests/test_report_qa.py`, `tests/test_web.py`: Unit/API/UI-contract tests.
- `README.md`, `config/config.example.yaml`: Explain storage, privacy and usage without a real Key.

## Task 1: Local Key Store and Precedence

**Files:** Create `qq_digest/ai/key_store.py`, `tests/test_ai_settings.py`; modify `qq_digest/config.py`.

- [ ] **Step 1: Write failing tests.** Assert a UI Key saved to a temporary path is returned by `Config.resolve_api_key()` ahead of an environment Key; old env/file fallback works if UI file is absent; empty/whitespace/multiline inputs fail; replacement never returns the old Key. Example:

```python
def test_ui_key_takes_precedence(tmp_path, monkeypatch, config):
    config.ai.ui_api_key_file = str(tmp_path / "deepseek-api-key.txt")
    monkeypatch.setenv(config.ai.api_key_env, "env-key")
    save_ui_api_key(Path(config.ai.ui_api_key_file), "ui-key")
    assert config.resolve_api_key() == "ui-key"
```

- [ ] **Step 2: Run red test.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_settings.py -q`; expected failure: `save_ui_api_key`/`ui_api_key_file` not defined.
- [ ] **Step 3: Implement the store.** Define `save_ui_api_key(path: Path, value: str) -> None`, `ui_api_key_exists(path: Path) -> bool`, and `read_ui_api_key(path: Path) -> str | None`. Strip one trailing newline only; reject blank, embedded whitespace/newline, and oversized values. Create a dedicated secrets directory; on Windows restrict its DACL to current user, SYSTEM and Administrators before writing. Write a same-directory temporary file, flush/fsync, restrict its ACL, then `os.replace`; on failure remove only that temp file and leave the previous Key. Never include value in exceptions/logs. Add `AIConfig.ui_api_key_file` defaulting to `D:\Dev\QQDigest\secrets\deepseek-api-key.txt` for this Windows deployment; `resolve_api_key()` checks valid UI file first, then unchanged legacy order.
- [ ] **Step 4: Run green and regression tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_settings.py tests/test_config.py -q`; expected all pass.
- [ ] **Step 5: Commit.** Stage only the named files; commit `feat: add local DeepSeek key store`.

## Task 2: Authenticated Settings Page and APIs

**Files:** Modify `qq_digest/web/app.py`, `qq_digest/web/templates/base.html`, `tests/test_web.py`; create `qq_digest/web/templates/ai_settings.html`.

- [ ] **Step 1: Write failing route tests.** Unauthenticated GET/PUT/test endpoints must reject. Authenticated GET returns `base_url`, `model`, `key_configured`, never Key. PUT with a valid Key invokes the real temporary key store and returns only status; invalid Key is 422. Test-connection failure returns a sanitized message and does not echo Key.

```python
assert client.get("/api/ai-settings").status_code == 401
client.post("/login", data={"password": "password123"})
payload = client.get("/api/ai-settings").json()
assert "api_key" not in payload
assert payload["model"] == "gpt-test"
```

- [ ] **Step 2: Run red test.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k ai_settings`; expected 404/missing template.
- [ ] **Step 3: Implement endpoints and page.** Add `GET /ai-settings`, `GET /api/ai-settings`, `PUT /api/ai-settings/key`, and `POST /api/ai-settings/test` under `require_login`. PUT calls `save_ui_api_key` in a worker thread; test creates the compatible `AIClient`, sends a minimal JSON request without chat records, closes it, and maps `AIError` to a redacted status. The template uses a password input with no value attribute, `autocomplete="new-password"`, and a visible configured/not-configured state. Add one nav link to the page.
- [ ] **Step 4: Run green tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k ai_settings`; expected pass.
- [ ] **Step 5: Commit.** Stage only these app/UI/test files; commit `feat: configure DeepSeek key in UI`.

## Task 3: Bounded Report Context

**Files:** Create `qq_digest/report_qa.py`, `tests/test_report_qa.py`.

- [ ] **Step 1: Write failing tests.** Daily boundaries cover exactly one local day; range boundaries include the end day; a different group's messages never appear. When all messages fit, preserve chronological order; when over budget, select question-matching messages plus immediate neighbors, without exceeding the character budget. Empty archives raise a controlled `NoReportEvidence` exception.

```python
context = select_report_context(messages, "为什么报错 503？", max_chars=400)
assert "503" in context.text
assert context.message_count <= len(messages)
assert len(context.text) <= 400
```

- [ ] **Step 2: Run red test.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_report_qa.py -q`; expected missing module/function.
- [ ] **Step 3: Implement pure context selection and report lookup.** `ReportQAService` loads the report row by typed ID, derives `group_id` and local `[start, end)` dates, calls `Archive.messages_in_window`, and never accepts group/date from the request. `select_report_context` formats `[msg_id|time|sender] text`, sends the full window when it fits, otherwise scores Chinese character bigrams and alphanumeric words from the question/recent user turns, adds neighboring messages, and returns selected IDs/count/truncation. Input and output lengths are bounded.
- [ ] **Step 4: Run green test.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_report_qa.py -q`; expected pass.
- [ ] **Step 5: Commit.** Stage only service/test files; commit `feat: select report-scoped chat context`.

## Task 4: Grounded Answer and Ask API

**Files:** Modify `qq_digest/report_qa.py`, `qq_digest/web/app.py`, `tests/test_report_qa.py`, `tests/test_web.py`.

- [ ] **Step 1: Write failing tests.** Valid questions and at most six recent turns reach a fake JSON AI client; returned citation IDs outside selected context are dropped; a response with no valid evidence states uncertainty. Missing report, missing Key, no original records, oversized question/history, and provider failure return bounded, non-secret errors. Verify day/range route behavior and authentication.

```python
response = client.post("/api/reports/daily/1/ask", json={"question": "原因是什么？", "history": []})
assert response.status_code == 200
assert set(response.json()) >= {"answer", "sources", "context_message_count", "context_truncated"}
```

- [ ] **Step 2: Run red tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_report_qa.py tests/test_web.py -q -k 'report_qa or ask_report'`; expected route/service failures.
- [ ] **Step 3: Implement answer method and route.** Validate question/history with Pydantic limits; combine report Markdown, selected original-message context, and recent dialogue into system/user messages; ask the existing `AIClient` for JSON `{"answer": "...", "source_ids": ["..."]}`. Rehydrate source metadata only for IDs sent in this request, never trust model-provided source text, and return short excerpts. Call blocking AI work via `asyncio.to_thread`; close client in `finally`. Render chat content as untrusted evidence, not instructions. Return clear 404/422/503 errors without request payloads or Key.
- [ ] **Step 4: Run green and full backend tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_report_qa.py tests/test_web.py -q`; expected pass.
- [ ] **Step 5: Commit.** Stage named files; commit `feat: answer report questions with checked citations`.

## Task 5: Ephemeral Report Dialogue

**Files:** Modify `qq_digest/web/templates/reports.html`, `tests/test_web.py`.

- [ ] **Step 1: Write failing UI-contract tests.** Reports page contains a report-level ask button, dialog role/label, input, privacy note and source-list container. Script clears history when closing/switching reports and escapes model/source text.

```python
page = client.get("/reports").text
assert 'id="report-qa-dialog"' in page
assert '将选中的聊天片段发送到 DeepSeek' in page
```

- [ ] **Step 2: Run red test.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k report_qa_dialog`; expected missing markup.
- [ ] **Step 3: Implement UI.** Show “问 DeepSeek” only for an opened report; use the existing drawer/modal styling. Keep `qaHistory` only in JS memory and cap to six turns; clear on close/report switch. On submit, disable repeated requests, call the typed ask route, append answer as text plus server-validated source excerpts, show reference count/truncation, and restore focus on close. At mobile width keep input/button/source readable. Do not use `innerHTML` with unescaped AI strings.
- [ ] **Step 4: Run green UI-contract tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k report_qa_dialog`; expected pass.
- [ ] **Step 5: Commit.** Stage named files; commit `feat: add report Q&A dialog`.

## Task 6: Documentation, Verification, Deployment

**Files:** Modify `README.md`, `config/config.example.yaml`.

- [ ] **Step 1: Document the final paths and privacy behavior.** Explain UI Key precedence, `D:\Dev\QQDigest\secrets\deepseek-api-key.txt`, one-time connection test, report-scoped excerpts sent to DeepSeek, and non-persistent Q&A. Do not include a real Key or chat content.
- [ ] **Step 2: Run full checks.** `D:\CodexTools\python\Scripts\python.exe -m pytest -q`, `git diff --check`; expected zero failures/errors.
- [ ] **Step 3: Browser QA.** Restart the existing local `qq-digest serve` process only after verifying its command line, then check `/ai-settings` and `/reports` in the in-app browser at desktop and phone widths; exercise dialog, sources, errors and console. Do not submit a real question or Key unless the user supplies one in the UI.
- [ ] **Step 4: Review and integrate.** Review the diff; merge the isolated worktree into the original checkout without deleting `qq_digest/web/templates/reports (1).html`; verify tests again, restart the live service from the original checkout, and push non-force to `origin/main` only if remote has not moved.
- [ ] **Step 5: Confirm.** Compare local and remote SHAs, confirm HTTP 200 on the local service, and report the paths changed. Keep the real secret file absent until the user enters a Key.
