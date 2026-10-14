# 待办异步完整性实施计划

> **For agentic workers:** 使用 dispatching-parallel-agents 分开处理已确认的 UI 与 API 问题，执行 TDD、独立审查和 verification-before-completion；用户已授权当前会话自主执行。

**Goal:** 保存等待期间保留新草稿，旧创建响应不影响新来源面板，SQLite 越界参数返回验证错误。

**Architecture:** 复用 taskDrafts/taskPanelToken；保存成功只收敛已提交快照，额外保存中状态仅按任务 ID 表示真实在途请求。API/服务沿用字段范围校验，不新增框架。

**Tech Stack:** Python/FastAPI/SQLite、Jinja/原生 JavaScript、pytest/CJS、独立 Playwright。

- [x] 确认基线、复用干净受管 worktree；40 项既有专项通过。
- [x] 新增并运行 RED：新草稿消失、新面板被旧创建关闭、revision 越界抛异常。
- [ ] `tasks.html` 保存／面板回调最小修复；添加列表重建与重复提交保护回归。
- [ ] `task_routes.py`／`task_inbox.py` 越界回归及最小校验；合法值正常、原状态未变。
- [ ] 同类异步流程定点检查；仅对确定复现的问题扩大当前修复。
- [ ] 专项 GREEN、独立代码质量复核、真实浏览器交互。
- [ ] 全量 pytest 和 CJS；记录边界、更新路线图；合入主项目并验证桌面包和回退入口。

证据：`D:\Cache\QQDigestFollowup-20261014`，Python：`D:\CodexTools\python\Scripts\python.exe`。CLI 与生产外部链路本轮不调用。
