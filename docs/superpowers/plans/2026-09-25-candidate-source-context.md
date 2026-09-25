# Candidate Source Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show cited original QQ messages plus two neighboring messages on each side in candidate review, including existing candidates.

**Architecture:** A read-only candidate-context service resolves only the candidate's own group and message IDs, fetches adjacent archived rows in stable order, deduplicates and bounds the result. The authenticated candidate-detail API adds this data without changing the candidate schema. The existing drawer displays the messages as plain text with citation markers and a missing-source fallback.

**Tech Stack:** Python 3.11+, SQLite, FastAPI, Jinja/vanilla JavaScript, pytest; Python executable `D:\CodexTools\python\Scripts\python.exe`.

---

## File map

- `qq_digest/candidate_context.py`: Query archived source messages and their neighbors, group-scoped and bounded.
- `qq_digest/web/app.py`: Add context fields to the authenticated detail response only.
- `qq_digest/web/templates/candidates.html`: Render source/context rows safely in the existing drawer.
- `tests/test_candidate_context.py`: Boundary, deduplication, isolation, missing-source, length/limit tests.
- `tests/test_web.py`: API and page-contract tests.

Preserve the existing untracked `qq_digest/web/templates/reports (1).html`. Do not change candidate generation or database schema. Use an isolated worktree for implementation; do not put a real Key or chat contents in tests, commits, or terminal output.

## Task 1: Read-only context selection

**Files:** Create `qq_digest/candidate_context.py`, `tests/test_candidate_context.py`.

- [ ] **Step 1: Write failing unit tests.** Create an archive with two groups and synthetic `NormalizedMessage` rows at known timestamps. Construct a `SummaryCandidate` with `message_ids=["m2", "m3"]`. Assert `candidate_source_context(archive, candidate)` returns only group 1 rows ordered by `(timestamp, msg_id)`, marks m2/m3 cited, includes at most two preceding and two following each, and deduplicates overlap. Add separate cases for an edge-of-group citation, a missing ID, 45 cited messages (only earliest 40 shown with `source_context_truncated=True`), and a 2001-character message (`text_truncated=True`, text length 2000). Example assertion:

```python
result = candidate_source_context(archive, candidate)
assert [row["msg_id"] for row in result["source_context"]] == ["m0", "m1", "m2", "m3", "m4", "m5"]
assert [row["msg_id"] for row in result["source_context"] if row["is_cited"]] == ["m2", "m3"]
assert result["missing_source_count"] == 0
```

- [ ] **Step 2: Run red.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_candidate_context.py -q`; expected import error for missing `qq_digest.candidate_context`.

- [ ] **Step 3: Implement `candidate_source_context`.** Use exact result keys `source_context`, `source_context_truncated`, `missing_source_count`. Use the candidate's own `group_id` in every SQL query, never a client-supplied group. Use these queries for each existing cited row (repeat the second query with `>` and ascending order for following rows):

```python
source = archive.connection.execute(
    "SELECT * FROM messages WHERE group_id=? AND msg_id=?",
    (candidate.group_id, msg_id),
).fetchone()
before = archive.connection.execute(
    """SELECT * FROM messages WHERE group_id=?
       AND (timestamp < ? OR (timestamp = ? AND msg_id < ?))
       ORDER BY timestamp DESC, msg_id DESC LIMIT 2""",
    (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
).fetchall()
after = archive.connection.execute(
    """SELECT * FROM messages WHERE group_id=?
       AND (timestamp > ? OR (timestamp = ? AND msg_id > ?))
       ORDER BY timestamp, msg_id LIMIT 2""",
    (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
).fetchall()
```

The full new module is:

```python
"""Bounded original-message context for candidate review."""

from __future__ import annotations

from .archive import Archive
from .models import SummaryCandidate


def candidate_source_context(archive: Archive, candidate: SummaryCandidate) -> dict:
    ids = list(dict.fromkeys(candidate.message_ids))
    cited_rows = {}
    for msg_id in ids:
        row = archive.connection.execute(
            "SELECT * FROM messages WHERE group_id=? AND msg_id=?",
            (candidate.group_id, msg_id),
        ).fetchone()
        if row is not None:
            cited_rows[msg_id] = row
    all_rows = dict(cited_rows)
    for source in cited_rows.values():
        before = archive.connection.execute(
            """SELECT * FROM messages WHERE group_id=?
               AND (timestamp < ? OR (timestamp = ? AND msg_id < ?))
               ORDER BY timestamp DESC, msg_id DESC LIMIT 2""",
            (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
        ).fetchall()
        after = archive.connection.execute(
            """SELECT * FROM messages WHERE group_id=?
               AND (timestamp > ? OR (timestamp = ? AND msg_id > ?))
               ORDER BY timestamp, msg_id LIMIT 2""",
            (candidate.group_id, source["timestamp"], source["timestamp"], source["msg_id"]),
        ).fetchall()
        for row in (*before, *after):
            all_rows[row["msg_id"]] = row
    order = lambda row: (row["timestamp"], row["msg_id"])
    chosen = {row["msg_id"]: row for row in sorted(cited_rows.values(), key=order)[:40]}
    for row in sorted(all_rows.values(), key=order):
        if len(chosen) >= 40:
            break
        chosen[row["msg_id"]] = row
    selected = sorted(chosen.values(), key=order)
    return {
        "source_context": [
            {
                "msg_id": row["msg_id"],
                "timestamp": row["timestamp"],
                "sender_qq": row["sender_qq"],
                "text": row["text"][:2000],
                "is_cited": row["msg_id"] in cited_rows,
                "text_truncated": len(row["text"]) > 2000,
            }
            for row in selected
        ],
        "source_context_truncated": len(all_rows) > len(chosen),
        "missing_source_count": len(ids) - len(cited_rows),
    }
```

Keep message text out of exceptions/logs. If none of the cited IDs are archived in the group, return an empty `source_context` and the missing count.

- [ ] **Step 4: Run green.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_candidate_context.py -q`; expected all pass.
- [ ] **Step 5: Commit.** Stage only `qq_digest/candidate_context.py` and `tests/test_candidate_context.py`; commit `feat: load candidate source context`.

## Task 2: Candidate detail API

**Files:** Modify `qq_digest/web/app.py`, `tests/test_web.py`.

- [ ] **Step 1: Write failing API tests.** Create a candidate in group 123 citing `m2`, archive synthetic `m1`/`m2`/`m3` in group 123 and an `m2` in group 999. Assert unauthenticated GET is 401; authenticated GET returns `source_context` IDs m1/m2/m3 with m2 cited, `missing_source_count=0`, and existing `excerpt` and `message_ids` unchanged. Add a candidate citing only a missing ID; assert empty context, missing count 1, and original excerpt preserved. Example:

```python
detail = client.get(f"/api/candidates/{candidate_id}").json()
assert [row["msg_id"] for row in detail["source_context"]] == ["m1", "m2", "m3"]
assert detail["excerpt"] == "旧摘录"
```

- [ ] **Step 2: Run red.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k candidate_source_context`; expected missing `source_context` key.
- [ ] **Step 3: Implement the narrow API addition.** Import `candidate_source_context` in `qq_digest/web/app.py` and add its mapping only to `api_candidate_detail`:

```python
return {
    **item.model_dump(),
    "group_name": group["name"] if group else str(item.group_id),
    **candidate_source_context(_archive(request), item),
}
```

Do not add full chat text to `/api/candidates` list responses. Existing `require_login` remains before candidate lookup.

- [ ] **Step 4: Run green and regression tests.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k candidate`; expected pass.
- [ ] **Step 5: Commit.** Stage only app/test files; commit `feat: expose candidate source context in detail`.

## Task 3: Review drawer and verification

**Files:** Modify `qq_digest/web/templates/candidates.html`, `tests/test_web.py`.

- [ ] **Step 1: Write a failing page-contract test.** Authenticated `/candidates` HTML must contain a context host and citation styling, use `textContent` for message text, show missing/truncation notices, and preserve the current confirm/ignore/later forms. Assert for the following stable markers:

```python
page = client.get("/candidates").text
assert 'class="candidate-source-context"' in page
assert 'item.text' in page and 'textContent' in page
assert 'is_cited' in page
assert 'missing_source_count' in page
```

- [ ] **Step 2: Run red.** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_web.py -q -k candidate_context_drawer`; expected missing context markup.
- [ ] **Step 3: Implement safe display.** Add `<section id="candidate-source-context" class="candidate-source-context" aria-label="原文与上下文"></section>` after `candidate-detail-content` inside the existing drawer. Keep escaped metadata and message-ID rendering in `viewCandidate` but remove its `['原文摘录', c.excerpt]` row. After metadata is rendered, call this helper. Use a monotonically increasing `candidateViewToken` in `viewCandidate` and increment it in `closeCandidateDetail`, ignoring stale GET responses.

```javascript
function renderCandidateContext(c) {
  var host = document.getElementById('candidate-source-context');
  host.textContent = '';
  var title = document.createElement('h3'); title.textContent = '原文与上下文'; host.appendChild(title);
  var rows = c.source_context || [];
  rows.forEach(function(item) {
    var row = document.createElement('div'); row.className = 'source-context-row' + (item.is_cited ? ' is-cited' : '');
    var meta = document.createElement('div'); meta.className = 'source-context-meta';
    meta.textContent = new Date(item.timestamp).toLocaleString('zh-CN') + ' · ' + (item.sender_qq || '未知发送者') + (item.is_cited ? ' · 候选引用' : ' · 相邻消息');
    var body = document.createElement('div'); body.className = 'source-context-text'; body.textContent = item.text;
    row.appendChild(meta); row.appendChild(body);
    if (item.text_truncated) { var shortNote = document.createElement('small'); shortNote.textContent = '原消息过长，已截断展示'; row.appendChild(shortNote); }
    host.appendChild(row);
  });
  if (!rows.length) { var fallback = document.createElement('div'); fallback.textContent = c.excerpt || '没有可用的归档原消息'; host.appendChild(fallback); }
  if (c.missing_source_count) { var missing = document.createElement('p'); missing.textContent = c.missing_source_count + ' 条引用原消息未在归档中找到'; host.appendChild(missing); }
  if (c.source_context_truncated) { var limit = document.createElement('p'); limit.textContent = '消息较多，仅展示前 40 条（优先候选引用）'; host.appendChild(limit); }
}

var candidateViewToken = 0;
function viewCandidate(id) {
  var token = ++candidateViewToken;
  api('GET', '/api/candidates/' + id).then(function(c) {
    if (token !== candidateViewToken) return;
    var messages = (c.message_ids || []).map(function(v) { return '<code>' + escapeHtml(v) + '</code>'; }).join(' ');
    var rows = [
      ['标题', c.title], ['状态', candidateStatusLabels[c.status] || c.status],
      ['类型', candidateTypeLabels[c.candidate_type] || c.candidate_type],
      ['来源群', c.group_name], ['日期', c.created_date], ['链接', c.link],
      ['内容', c.content], ['价值理由', c.reason], ['忽略原因', c.ignore_reason]
    ];
    document.getElementById('candidate-detail-content').innerHTML = rows.filter(function(r) { return r[1]; }).map(function(r) {
      return '<div class="detail-row"><div class="detail-label">' + escapeHtml(r[0]) + '</div><div>' + escapeHtml(r[1]) + '</div></div>';
    }).join('') + '<div class="detail-row"><div class="detail-label">消息引用</div><div>' + messages + '</div></div>';
    renderCandidateContext(c);
    document.getElementById('candidate-detail').hidden = false;
    document.getElementById('candidate-detail-backdrop').hidden = false;
  }).catch(function(e) { if (token === candidateViewToken) toast('加载详情失败: ' + e.message, 'error'); });
}
function closeCandidateDetail() {
  candidateViewToken += 1;
  document.getElementById('candidate-detail').hidden = true;
  document.getElementById('candidate-detail-backdrop').hidden = true;
}
```

Set `.candidate-source-context { max-height: 360px; overflow-y: auto; }`, `.source-context-row { padding: 9px; border-bottom: 1px solid var(--border); }`, `.source-context-row.is-cited { border-left: 3px solid var(--primary); background: var(--primary-soft); }`, `.source-context-text { white-space: pre-wrap; overflow-wrap: anywhere; }`, and a smaller dimmed `.source-context-meta`. Do not interpolate original message text into `innerHTML`.
- [ ] **Step 4: Run green and full checks.** `D:\CodexTools\python\Scripts\python.exe -m pytest -q`, then `git diff --check`; expected 0 failures and no whitespace errors.
- [ ] **Step 5: Browser QA.** Restart only the verified local `qq-digest serve` instance. In the in-app browser, open a candidate with archived sources; verify visible cited and neighboring messages, missing-source fallback on a synthetic test case, drawer close, no console errors, desktop and 390-pixel mobile widths. Do not submit any AI request.
- [ ] **Step 6: Integrate and publish.** Review the diff; fast-forward merge from the isolated worktree without touching `reports (1).html`; re-run full tests, verify local HTTP 200, then push non-force to `origin/main` if remote has not moved. Confirm local/remote SHAs match. Keep the host-managed worktree intact.
