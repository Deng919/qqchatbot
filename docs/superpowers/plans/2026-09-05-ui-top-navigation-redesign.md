# QQ Digest Top Navigation UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有侧栏式 Web UI 改造成用户确认的顶部导航数据台，并完成桌面与手机端适配。

**Architecture:** 继续使用 FastAPI、Jinja2 和原生 JavaScript，不修改业务 API。共享设计令牌、导航、页面工具栏、表格和响应式规则集中在 `base.html`，各业务页面只保留自身布局与动态渲染逻辑。

**Tech Stack:** FastAPI、Jinja2、原生 CSS、原生 JavaScript、pytest、真实浏览器 QA

---

### Task 1: 顶部导航基础框架

**Files:**
- Modify: `tests/test_web.py`
- Modify: `qq_digest/web/templates/base.html`

- [x] **Step 1: 添加顶部导航结构测试**

在 `test_operational_ui_contains_group_search_and_safe_render_helpers` 中断言：

```python
assert 'class="global-header"' in dashboard.text
assert 'class="global-nav"' in dashboard.text
assert 'class="page-toolbar"' in dashboard.text
assert 'class="sidebar"' not in dashboard.text
```

- [x] **Step 2: 运行测试并确认旧结构导致失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py::test_operational_ui_contains_group_search_and_safe_render_helpers -q`

Expected: FAIL，页面仍包含 `sidebar`，且没有 `global-header`。

- [x] **Step 3: 重构共享页面壳层和设计令牌**

将 `base.html` 的结构改为：

```html
<header class="global-header">
  <div class="global-header-inner">
    <a class="brand" href="/">群聊简报 <small>QQ Digest</small></a>
    <nav class="global-nav" aria-label="主要导航">...</nav>
  </div>
</header>
<main id="main-content">
  <header class="page-toolbar">
    <h1>...</h1>
    <div class="actions">...</div>
  </header>
  <div class="content">...</div>
</main>
```

共享 CSS 使用 52px 顶部栏、最大 1440px 内容容器、4px 以内控件圆角、tabular 数字和 160ms 状态变化。小于 700px 时，品牌栏与导航分两行，导航横向滚动，页面工具栏改为纵向。

- [x] **Step 4: 运行 Web 目标测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: PASS。

### Task 2: 总览数据台与手机任务列表

**Files:**
- Modify: `tests/test_web.py`
- Modify: `qq_digest/web/templates/dashboard.html`

- [x] **Step 1: 添加总览响应式语义测试**

```python
assert 'class="table responsive-table job-table"' in dashboard.text
assert "data-label=\"任务\"" in dashboard.text
assert 'class="dashboard-grid"' in dashboard.text
```

- [x] **Step 2: 运行测试并确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py::test_dashboard_renders_job_errors_with_html_escaping -q`

Expected: FAIL，旧模板没有响应式任务表语义。

- [x] **Step 3: 改造指标、任务和群组活跃度**

为动态任务单元格添加 `data-label`：

```javascript
return '<tr><td data-label="任务">' + ...
  + '<td data-label="状态">' + ...
  + '<td data-label="开始">' + ...
  + '<td data-label="结束">' + ...
  + '<td data-label="错误">' + ...;
```

桌面保持两列数据区；手机端 `.responsive-table` 隐藏表头，每个 `tr` 变为边框分隔的任务条目，每个 `td::before` 使用 `data-label` 展示字段名。

- [x] **Step 4: 运行总览测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: PASS。

### Task 3: 群组、采集和摘要页面整理

**Files:**
- Modify: `qq_digest/web/templates/groups.html`
- Modify: `qq_digest/web/templates/collect.html`
- Modify: `qq_digest/web/templates/reports.html`
- Modify: `tests/test_web.py`

- [x] **Step 1: 添加页面类名和无内联宽度测试**

```python
assert 'class="group-config-table"' in groups.text
assert 'class="filter-toolbar collect-toolbar"' in collect.text
assert 'class="report-layout"' in reports.text
assert 'style="width:150px"' not in groups.text
```

- [x] **Step 2: 运行测试并确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: FAIL，旧页面仍依赖内联宽度。

- [x] **Step 3: 改造三个页面**

群组页将搜索和扫描保留在页面工具栏，配置表使用 `group-config-table`，群名列在桌面端 sticky；输入宽度由 `.keyword-field` 和 `.days-field` 管理。采集页把群、日期、采集和刷新放进 `.filter-toolbar.collect-toolbar`，移动端使用两列日期和全宽群选择。摘要页采用 `.report-layout`，桌面列表与详情自然分区，手机单列堆叠。

- [x] **Step 4: 运行 Web 测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: PASS。

### Task 4: 候选审核与登录页统一

**Files:**
- Modify: `qq_digest/web/templates/candidates.html`
- Modify: `qq_digest/web/templates/login.html`
- Modify: `tests/test_web.py`

- [x] **Step 1: 添加审核抽屉和登录品牌测试**

```python
assert 'class="candidate-detail-backdrop"' in candidates.text
assert 'class="login-shell"' in login.text
assert 'aria-label="关闭候选详情"' in candidates.text
```

- [x] **Step 2: 运行测试并确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: FAIL，旧审核详情没有遮罩，登录页未使用统一命名。

- [x] **Step 3: 完成审核和登录视觉状态**

候选详情采用固定右侧抽屉和半透明遮罩，关闭按钮有明确可访问名称；打开和关闭函数同时切换遮罩。筛选器在桌面为紧凑网格、手机为两列，搜索和提交按钮跨整行。登录页复用相同颜色、字体、边框和焦点标准，但保持独立模板以避免未登录时加载业务导航。

- [x] **Step 4: 运行 Web 测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests\test_web.py -q`

Expected: PASS。

### Task 5: 全量验证和真实浏览器 QA

**Files:**
- Modify: `docs/superpowers/plans/2026-09-05-ui-top-navigation-redesign.md`

- [x] **Step 1: 运行静态与全量测试**

Run:

```powershell
D:\CodexTools\python\Scripts\python.exe -m compileall -q qq_digest tests
D:\CodexTools\python\Scripts\python.exe -m pytest -q
git diff --check
```

Expected: compileall 退出码 0，全部测试通过，diff 无空白错误。

- [x] **Step 2: 重启 Web 服务**

停止当前 8765 监听进程，以配置的会话密钥重新运行：

```powershell
if (-not $env:QQ_DIGEST_SESSION_SECRET) { throw "QQ_DIGEST_SESSION_SECRET 未设置" }
D:\CodexTools\python\Scripts\qq-digest.exe serve --config-path config/config.yaml
```

Expected: `http://127.0.0.1:8765/login` 返回 200。

- [x] **Step 3: 桌面浏览器验证**

在 1440×900 验证总览、群组、采集、摘要和审核页面：顶部导航当前态正确，页面无重叠，群组表格局部滚动，主要操作可见，控制台无错误。

- [x] **Step 4: 手机浏览器验证**

在 390×844 验证：页面本身无横向滚动，导航可横向滚动，总览任务转为列表，候选按钮和详情抽屉可操作，群组表格只在自身容器滚动。

- [x] **Step 5: 更新计划状态**

勾选已完成步骤，并记录最终测试数量和浏览器检查结果。
