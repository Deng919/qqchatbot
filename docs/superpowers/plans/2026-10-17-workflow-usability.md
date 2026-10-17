# 正常流程体验修复实施计划

> **For agentic workers:** 使用 `dispatching-parallel-agents` 分别处理无共享文件的三个问题；主代理合并验证，使用 `requesting-code-review` 独立复查。

**Goal:** 完成中断的正常流程体验验收，修复三处已确认的展示与布局问题。

**Architecture:** 继续使用共享结果渲染器、报告搜索服务及共享选群器。展示清理不写入原报告；选群调整只发生于生成面板首次挂载。

**Tech Stack:** Python、FastAPI、SQLite、Jinja、原生 JavaScript、pytest、Node CJS、独立 Chromium／Playwright。

## 任务 1：更新结果阶段文案

文件：`qq_digest/web/templates/group_batch_feedback.html`、`tests/group_batch_ui.cjs`。

- [ ] 扩充真实渲染器的最小 DOM 测试，检查 dry-run 的“待补缺口”和实际成功更新的“本次补齐”，再检查失败不宣称已补齐。
- [ ] `node tests/group_batch_ui.cjs`，记录断言失败，排除测试装配错误。
- [ ] 最小实现：`payload.dry_run ? '待补缺口：' : row.status === 'success' ? '本次补齐：' : '更新前缺口：'`。
- [ ] 重跑上述脚本及 `pytest tests/test_group_batch.py`；不改变 JSON 接口、计数或重试范围。

## 任务 2：报告展示片段

文件：`qq_digest/search.py`、`qq_digest/web/templates/summary_reading_script.html`、`tests/test_report_search_display.py`、`tests/summary_clean_display.cjs`。

- [ ] 用真实 `Archive` 写入日报、范围报告和消息，搜索同一关键词。报告断言无系统引用、标题与加粗定界符，消息断言保留原文；校验原报告字节未变。
- [ ] 覆盖普通乘法、代码内容、URL、日报／范围、排序及原始匹配范围，运行新增测试，保存预期失败。
- [ ] 在报告分支调用 `_snippet` 前清理可读文本；消息与知识分支不改，匹配仍基于原始 body。
- [ ] 为现有 `summaryCleanText` 的 Markdown 链接补回归，然后只增加安全文本替换；保留代码块原文。
- [ ] `pytest tests/test_report_search_display.py tests/test_search_pagination.py tests/test_message_search.py` 与 `node tests/summary_clean_display.cjs` 均通过。

## 任务 3：生成面板

文件：`qq_digest/web/templates/summary_generation_script.html`、`qq_digest/web/templates/reports.html`、`tests/summary_selection.cjs`。

- [ ] 在已有 CJS 测试加入生成选群挂载场景，验证单个可用群的默认选择、已有多群选择、显式空选择、用户清空后不恢复。
- [ ] `node tests/summary_selection.cjs`，保存默认单群尚未选择等预期失败。
- [ ] 首次挂载选群器时才在无预选、非显式空、只有一个启用群时设置该群；`open: !ids.length`。移除外层 `range-group-list` 的重复滚动限制。
- [ ] 重跑 `node tests/summary_selection.cjs` 与 `pytest tests/test_summary_generation_ui.py`。
- [ ] 在真实浏览器 390×620 和桌面宽屏验证控件可达、群列表可操作、数量预览及日期保留。

## 合并与验收

- [ ] 基线相关测试通过，代理各自只改负责文件，不并行操作 Git 索引。
- [ ] 合并后跑相关回归与全部 CJS，再执行全量 pytest；输出保存在 D 盘本轮目录，避免生成输出进入源码目录。
- [ ] 独立浏览器用隔离合成数据点击走完正常业务流程，保存截图与可复现步骤。无需真实 AI、QQ、通知或用户凭据。
- [ ] 独立规格和质量复查，处理有证据的问题；不因为流程要求重复无必要测试。
- [ ] 记录验收、更新路线图，提交明确文件集合，推送 `codex/workflow-usability-20261017` 并核对远端 SHA。
- [ ] 若交付桌面版，使用新的日期／功能目录并保留当前正式版作为回退；不改 Codex 安装或全局安全设置。
