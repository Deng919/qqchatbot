# 待办草稿与重试恢复实施计划

> 按用户已确认的当前会话自主执行方式，使用 executing-plans 逐项实施。

**Goal:** 修复交互审查 17、18、19、26，让刷新与重试保留当前用户操作。

**Architecture:** 仅修改对应模板。待办建议按 key 暂存当前页草稿，已消失建议移除；列表请求用递增 token 防乱序。知识保存分开初始化与读取，重试只读当前目标。提醒页复用已有请求 token，数量缩小时重新读取最后有效页。数据结构、API 与权限不变。

**Tech Stack:** Jinja HTML、原生 JavaScript、Node VM 回归、现有 Python 测试。

## 方案与约束

保留全部建议卡片 DOM 会减少重建，但需维护卡片差量与排序；通用草稿框架会增加跨页面契约。本轮选择页面内 key 草稿和既有 token 模式，改动最小。草稿不落盘，不跨登录会话恢复；新的知识保存目标仍初始化，当前目标重试不初始化。正式数据和原未跟踪文件保留。

## Task 1 — 待办（17、18）

- [x] 新建 `tests/tasks_async.cjs`，执行真实 `tLoad/tRenderSuggestions/tForm`，用两个延迟 GET 乱序返回断言最新列表保留；编辑建议 B 后处理 A 并刷新，断言 B 标题／负责人／日期保留，A 草稿消失。
- [x] 运行 `node tests/tasks_async.cjs` 确認失败。
- [x] 修改 `qq_digest/web/templates/tasks.html`：声明 `taskLoadToken` 和 `taskSuggestionDrafts`；`tLoad` 捕获 `++taskLoadToken`，成功／失败回调均检查 token；`tForm` 可接收 `onDraft`，输入时保存三字段。`tRenderSuggestions` 合并 key 草稿并仅保留仍存在的 key。
- [x] 运行 `node tests/tasks_async.cjs` 与 `node tests/tasks_concurrency.cjs` 验证。

## Task 2 — 知识读取重试（19）

- [x] 扩充 `tests/knowledge_ui.cjs`：读取失败后填入标题／分类／说明／链接，点击实际重试按钮，断言重试期间及成功后输入和目标不变；已关闭或新目标不接受旧按钮结果。
- [x] 运行 `node tests/knowledge_ui.cjs` 确认失败。
- [x] 修改 `qq_digest/web/templates/knowledge_save.html`：初始化完成后 `return loadKnowledgeSave()`；读取函数捕获当前目标／新 token，清空读取结果并重置 ready，保留输入；重试按钮只在自身 token 仍有效时调用读取函数。
- [x] 运行 `node tests/knowledge_ui.cjs` 验证。

## Task 3 — 提醒页回退（26）

- [x] 扩充 `tests/reminder_ui.cjs`：请求第 2 页返回 total=20，断言重新请求第 1 页；响应后显示第 1 / 1 页；迟到越界响应不触发新请求。
- [x] 运行 `node tests/reminder_ui.cjs` 确认失败。
- [x] 修改 `qq_digest/web/templates/reminders_script.html`：token 校验后计算 `pages=Math.max(1,Math.ceil(data.total/data.page_size))`，`data.page>pages` 时 `return remindersLoad(pages)`，渲染仅使用有效页。
- [x] 运行 `node tests/reminder_ui.cjs` 验证。

## Task 4 — 验收与交付

- [x] `node --test tests/*.cjs`，相关 Python 与全量 Python；输出保存到 `D:\Cache\QQDigestInteractionRecovery-20261014`。
- [x] 使用独立浏览器进程验收真实页面，桌面／窄屏及代表性交互，不调用真实 QQ 或 AI，不使用内置浏览器关闭流程。
- [x] 独立复核，修复确定问题，更新路线图及交互审查状态。
- [x] 更新 desktop backend ID；构建隔离 EXE，健康／版本／清单及模板核对；正式包置于 `D:\Apps\QQDigestDesktop-2026-10-14-InteractionRecovery-Release`，旧包回退。
- [x] 提交、fast-forward 合并主分支；核对桌面入口和主目录原未跟踪文件。
