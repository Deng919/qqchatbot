# P1-05 提醒规则 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 按用户选择完成程序内＋Windows 提醒，覆盖四类规则、预览、免打扰及持久去重。

**Architecture:** ReminderService 独立 SQLite 工作连接，规则／游标／事件持久化；规则路由和页面独立。60 秒后台循环调用服务，再通过桌面注入的 Windows sink 投递汇总通知。

**Tech Stack:** Python、SQLite、FastAPI、Jinja2、原生 JavaScript、ctypes Win32、pytest、Node；Python 为 `D:/CodexTools/python/Scripts/python.exe`。

## 1. 核心服务及迁移

Create `qq_digest/reminders.py`、`tests/test_reminders.py`；modify `archive.py` 新表与群清理。核心契约：

```python
ReminderService(archive, timezone_name='Asia/Shanghai')
service.list_rules()  # {rules: [...]}；规则 DTO 含 rule_id, revision, enabled 等完整条件
service.save_rule(values, rule_id=None, expected_revision=None, now=None)
service.delete_rule(rule_id, expected_revision)  # 软删除
service.preview(values, now=None)  # {items,total,quiet,channels}，纯读取
service.scan(now=None)  # {created,queued}，持久游标与去重
service.list_events(page=1,page_size=20,unread_only=False)  # {items,total,unread,page,page_size}
service.mark_read(event_id, read=True)
service.dispatch_windows(sink, now=None)  # sink(title,body)->bool，最多一次聚合；无 sink 保留排队
```

- [x] RED：用实际归档消息／候选／待办／故障插入写测试，确认缺少 ReminderService 失败。
- [x] GREEN：参数化 SQL、严格 bool／ID／日期／时段／关键词校验、revision 冲突、群存在验证及 SQLite 新表，扫描基线采用实际 rowid／ID，任务对当前到期项有效。
- [x] 增加免打扰延后、重启、故障重复重试、已完成旧任务取消、每批 200 条不跳过剩余消息、软删除／重开、备份恢复的真实回归。
- [x] 定向 `-m pytest tests/test_reminders.py tests/test_archive.py tests/test_backup_restore.py`；独立需求后质量复审。

## 2. 接入及 Windows 通知

Create `qq_digest/web/reminder_routes.py`、`qq_digest/reminder_scheduler.py`、`qq_digest/windows_notifications.py`；modify features／web.features／operations／web.app／desktop.py。路由：GET `/reminders`，GET/POST `/api/reminder-rules`，PATCH/DELETE `/api/reminder-rules/{id}`，POST `/api/reminder-rules/preview`，GET `/api/reminders`，PATCH `/api/reminders/{id}`，POST `/api/reminders/check`，GET `/api/reminders/capabilities`。

- [x] RED：鉴权401、开关403、revision409、输入422、锁占用409、预览不投递／不写入、独立连接回滚测试；native sink 请求及清理失败测试。
- [x] GREEN：路由每次工作调用独立连接，锁内关闭；创建服务时使用实际 PRAGMA 路径。后台仅启用时运行，60 秒一轮；桌面可用能力经注入判断，不在浏览器冒充已投递。
- [x] Win32 适配器用隐藏窗口及 Shell_NotifyIconW，规定 API 原型、结构布局、图标／窗口资源清理。调用成功仅标系统接受。聚合最多一条横幅，抑制系统未知状态自动重发。
- [x] 生命周期启动／关闭与窗口通知资源退出覆盖定向回归，保持已有 QQ 日报通知兼容。

## 3. 页面和导航

Create `web/templates/reminders.html`、`reminders_script.html` 和交互 Node 回归；modify settings 导航、功能开关与构建清单。提醒页在设置下，页签“提醒列表／提醒规则”，预览、编辑、停用、软删除、已读、未读筛选及分页。

- [x] RED：迟到列表／预览不能覆盖新状态，编辑变化作废预览，重复保存阻止，错误保存保留输入，旧revision提示重开。
- [x] GREEN：安全文本节点，列表和对话框复用既有样式；规则字段显示类型、群、渠道、时段、关键词。Windows不可用时说明需桌面程序，不强制阻止保存Windows偏好。
- [x] 真实浏览器 1280／390 保存四类规则、预览、生成合成提醒、已读／分页，保留设置其他分类输入。

## 4. 验证与交付

- [x] 所有相关测试、完整 `-m pytest`、Python AST、Jinja 和 `git diff --check` 通过；独立需求及质量复审，修复具体问题。
- [x] README／ROADMAP记录规则口径、延后、Windows投递限制；路线图仅实际验收后标完成。
- [x] 新版 `D:/Apps/QQDigestDesktop-2026-10-05-Reminders`，缓存 `D:/Cache/QQDigestDesktop`；模板及模块核对，合成配置实际启动后还原 launcher。
- [x] 验证文件 `D:/Cache/QQDigestReminders-2026-10-05`；提交本地 `codex/reminder-rules-2026-10-05`，不推送。

验收：763 passed, 1 warning；独立复审、宽窄屏及实际 EXE 合成验证通过。详见 ../specs/2026-10-05-reminder-rules-verification.md。
