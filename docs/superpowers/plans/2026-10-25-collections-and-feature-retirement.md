# 收藏归并与功能退场 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 一个收藏入口承载内容保存和资料整理，删除话题跟踪与待办功能。

**Architecture:** 复用 bookmarks / knowledge 数据服务，由收藏页面选择视图。旧知识页面重定向兼容。退场功能移除路由注册与专属服务，历史表仍用于兼容旧数据库，不做数据删除。

**Tech Stack:** Python / FastAPI / Jinja / 原生 JavaScript / SQLite / PyInstaller。

**Spec:** `docs/superpowers/specs/2026-10-25-collections-and-feature-retirement.md`

## Global Constraints

- Python: `D:\CodexTools\python\Scripts\python.exe`。
- QA: `D:\Cache\QQDigestCollections-20261025`；程序包: `D:\Apps\QQDigestDesktop-2026-10-25-Collections-Release`。
- 不删历史个人数据；不提交本地配置、数据库、缓存或凭据。
- 当前工作分支 `codex/public-beta-2026-10-06`，验证后提交并推送。

## Review Focus

- 旧知识资料及筛选深链接可继续打开，不重复写入。
- 收藏开关关闭时资料整理入口仍有可用的退路。
- 已开启的话题、待办标志及旧到期提醒不能恢复退场功能。
- 收藏详情中的来源、整理操作不丢上下文；缺失原消息时保留快照。
- 生命周期退出、打包模板及模块清单不引用删除文件。

### Task 1: 退场话题与待办

**Files:** `qq_digest/features.py`, `qq_digest/web/features.py`, `qq_digest/web/app.py`, `qq_digest/web/background_tasks.py`, `qq_digest/reminders.py`, 对应专属服务、模板、测试。

- [x] 写退场 API、旧开关、旧提醒规则测试并观察失败。
- [x] 移除路由、服务注册与到期提醒链路，删除无调用专属文件；保留数据库历史表。
- [x] 更新受影响现有测试并运行，记录删除范围与兼容决定。

### Task 2: 收藏资料合并

**Files:** `qq_digest/web/bookmark_routes.py`, `qq_digest/web/knowledge_routes.py`, `qq_digest/web/templates/{base,section_navigation,primary_navigation,bookmarks,knowledge,knowledge_script,knowledge_save,find_script}.html`, 收藏脚本及新回归测试。

- [x] 写单入口、旧知识链接、资料视图、收藏详情整理与禁用收藏场景测试并观察失败。
- [x] 复用资料页内容和脚本，统一顶部导航与保存文案；删话题待办前端入口及专属模板。
- [x] Python 与 Node 回归、独立浏览器桌面和移动端检查。

### Task 3: 集成与桌面交付

**Files:** `scripts/build_desktop.py`, `qq_digest/desktop.py`, 验证记录。

- [x] 全分支独立审查，按风险运行相关与整套测试。
- [x] 更新后端标识与打包模板列表，构建并核对模块、模板清单。
- [x] 独立 WebView 验证，原生更新器切换，核对快捷方式与用户数据保留。
- [x] 提交、推送、核对 GitHub SHA；汇报实际结果及路径。

## Execution record

- 2026-10-25：内联实现收藏归并；独立功能域退场实现并行处理，末尾独立审查。
- Ruling: 保留普通摘要的 `main_topics` / `tasks` JSON 字段和历史表；它们属于已有摘要内容与备份兼容，独立追踪/待办的服务、接口、页面已删除。
- 旧 Python 包装测试仍引用已删待办/话题 CJS，审查发现后一起删除；前端图标回归同步移除退场话题场景。
- 52 个 Node 行为脚本通过；收藏/资料回归 44 项通过；退场相关 12 文件 303 项通过；完整 Python 回归 1828 通过、3 跳过。
- 源码独立 Chromium 已验证保存→资料、旧链接、审核、390px 与桌面布局；独立打包 WebView 已验证收藏与资料切换。

- 原生更新器已成功切换，实际收藏/资料页面、退场接口与 6 条资料数据保留核对通过；配置、资料文件及旧记录摘要哈希不变。
