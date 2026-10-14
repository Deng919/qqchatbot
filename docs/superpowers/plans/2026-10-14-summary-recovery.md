# 摘要与后台恢复实施计划

> **For agentic workers:** 使用 dispatching-parallel-agents 分别处理已确认的 frontend/backend 独立文件；TDD、requesting-code-review 与 verification-before-completion；当前会话自主执行。

**Goal:** 首次登录恢复重连实际进度，恢复范围与选群一致，一次后台异常不永久关闭循环。

**Architecture:** 复用 summaryProgress、GroupSelector 和后台循环；普通异常与取消分别按当前运行语义处理，不引入框架。

**Tech Stack:** FastAPI/Python/SQLite、Jinja/JavaScript、pytest/CJS、独立 Playwright。

- [x] 复用干净 worktree；35 项现有专项通过。
- [x] 初次恢复 401、迟到范围控件和三个后台循环 RED。
- [ ] `summary_progress_script.html` 最小修复与额外新输入保护回归。
- [ ] `web/background_tasks.py` 普通失败继续、失败状态和决策阶段恢复，保留取消／操作锁；对应测试先 RED。
- [ ] 独立复核、真实浏览器、相关 GREEN；知识保存和报告重试定点检查，只有实际新复现才扩展修复。
- [ ] 全量 Python/CJS；构建并验证隔离 EXE、代码／模板和清单。
- [ ] 合入主项目，验证专项、更新桌面入口／回退／路线图和验收边界。
