# MyCortex 消息提交实施计划

> 逐项运行验证，按用户已确认的手动和自动范围实施。

目标：给 QQ Digest 的原消息和有价值的知识条目统一提供手动保存及按群自动保存。

设计：[消息提交接口](../specs/2026-11-19-mycortex-message-interface-design.md)。复用 SQLite、FastAPI、Cockpit 以及 MyCortex 本机提交权限的 MCP，无新增依赖。

- [x] 在 `qq_digest/mycortex.py` 实现群内来源核验、Cockpit 提炼、精确引用校验和共享回执。`tests/test_mycortex_delivery.py` 覆盖非法引用、空结果、回执丢失与目标隔离。
- [x] 在 `qq_digest/mycortex_transport.py` 实现本机提交权限的 MCP 通信。使用真实 MCP 和独立的合成 MyCortex 库检查入库、重试与引文留存。
- [x] 在 `qq_digest/web/mycortex_routes.py` 加认证后的手动提交和群级启停。Web 生命周期处理摘要已发布的新增候选。测试群级开关、历史候选过滤、限次重试及真实 Cockpit 自动提炼入库。
- [x] 共用消息来源和手动保存按钮，在群详情展示自动设置及最近结果。独立 Chromium 验证桌面和窄屏下的手动保存及群开关持久化。
- [x] 更新 README、配置示例和项目记忆。提交并推送当前工作分支，通过 `git ls-remote` 核对发布提交。
