# 收藏与稍后处理 Implementation Plan

**Goal:** 完成路线图 P1-06，收藏四类内容并统一处理。

**Architecture:** BookmarkService 验证来源并保存 SQLite 快照；独立 bookmark_routes 提供认证 API；共享收藏按钮和独立列表使用现有 api、toast 与上下文组件。

**Tech Stack:** Python / SQLite / FastAPI / Jinja / vanilla JavaScript。

### 1. 来源与持久化
- [x] 写 tests/test_bookmarks.py：四类真实来源、伪造目标、幂等、状态冲突、分页搜索、重启、来源缺失与群删除。
- [x] 用 `D:/CodexTools/python/Scripts/python.exe -X utf8 -m pytest tests/test_bookmarks.py -q` 确认新增 API 测试失败。
- [x] archive.py 添加 bookmarks 表及群删除清理；新建 bookmarks.py，保存服务端快照，状态和删除执行 revision 比较。
- [x] 新建 web/bookmark_routes.py，路径参数严格验证，认证及 feature guard，使用 bookmark_mutation 互斥；app.py 注册。

### 2. 可见入口与页面
- [x] features.py 增加默认开启的 bookmarks；服务端和 live 导航均添加收藏。
- [x] 新建 bookmarks.html、bookmarks_script.html 与 bookmark_actions.html；列表支持筛选、分页、完成/恢复、取消和快照详情。
- [x] message_context_script、summary_reading_script、knowledge_script、candidates 增加来源旁收藏按钮。客户端只传标识；所有内容以 textContent 展示。
- [x] 摘要来源使用现有 CatchupService 要点 key，核对当前要点，拒绝已变化的内容；资源/知识按实际候选记录验证。
- [x] 新模板列入 scripts/build_desktop.py；更新 desktop backend ID。

### 3. 验证与交付
- [x] 跑定向和完整 pytest；解析全部 Jinja 与渲染后的 JavaScript；git diff --check。
- [x] 独立只读代码评审，修复实际问题。
- [x] 合成服务器浏览器验证四类收藏、状态、取消、原消息上下文、开关及 390 宽度。
- [x] 在 D:/Apps/QQDigestDesktop-2026-10-06-Bookmarks 打包；D:/Cache/QQDigestBookmarks-2026-10-06 存放验证资料。实际 EXE 用合成配置验证，结束恢复 launcher 配置。
- [x] 更新 ROADMAP 与 README，提交已验证代码。


## 验证记录

- 新增 API 测试 14 项通过，包含四类来源、日报／范围摘要、幂等、版本冲突、后台阅读、实际备份恢复及不安全链接。新页面脚本回归覆盖筛选竞态、旧响应、关闭详情、重复点击与失败重试。
- 74 个 qq_digest Python 模块、37 个模板、14 个页面内 104 段 JavaScript 解析通过。
- 独立只读复审通过；已修复读接口被后台任务阻止、超大 revision 和修改期间切换筛选的过期列表。
- 浏览器合成数据：摘要收藏、知识与资源收藏、消息上下文、完成／恢复／取消、关闭再开启及资源记录。390×844 无横向溢出，导航自动换行，浏览器无错误。
- 实际 EXE 合成配置验证：后端 ID 2026-10-06-bookmarks、登录、收藏保存／去重／上下文／完成／恢复及顶部入口。37 个包内模板逐文件与源码一致，6 个提醒和收藏模块存在。关闭测试 EXE 后 launcher 已恢复项目配置。未访问真实 QQ 或 AI。
- 发布：D:/Apps/QQDigestDesktop-2026-10-06-Bookmarks/QQDigestDesktop.exe。
- 验证资料：D:/Cache/QQDigestBookmarks-2026-10-06；截图 browser/desktop-bookmarks.png、browser/mobile-bookmarks.png、browser/release-bookmarks.png。

- 最终完整回归：778 passed, 1 warning in 233.58s；日志 full-pytest.log。现有 Starlette/httpx 弃用提示保留。
- 现有 D:/Desktop/QQ Digest.lnk 已核对并更新到本次发布目录；旧快捷方式备份在验证缓存 previous-QQ-Digest.lnk。
