# P1-05 提醒规则验收记录

日期：2026-10-05。需求：按路线图继续，用户选择程序内＋Windows 通知。实现设计见 [设计](2026-10-05-reminder-rules-design.md)，步骤见 [实施计划](../plans/2026-10-05-reminder-rules.md)。

## 实现及边界

- 四类规则、全部／指定群、独立默认关闭开关、只读预览、跨午夜免打扰、持久已读和渠道状态。
- 每分钟检查，只在运行期间工作。关键词／资源新建及重新启用记录基线；到期未完成待办当前即可提醒。命名／时段／渠道编辑保留未扫描来源。
- 消息采用单调持久 rowid，删除群后不复用；资源同群规范链接去重，已有链接不因新候选补发。规则软删除保留历史。
- 当前任务和故障投递前复核；故障校验使用完整持久来源，异常页的显示上限保持不变。Windows 只提交名称／数量汇总，不复制聊天或错误细节。
- 独立工作连接与操作互斥；网页请求取消后锁保留至线程完成。实际改变提醒功能开关时与投递互斥，忙碌返回 409 可稍后重试。
- Windows 超时或崩溃后结果未知，停止自动重投，允许明确手动重试。系统接受请求不保证横幅可见；未验证系统实际横幅展示，不修改通知／免打扰设置。

## 证据

完整命令：`D:/CodexTools/python/Scripts/python.exe -m pytest -o addopts='' -q --basetemp D:/Cache/QQDigestReminders-2026-10-05/final-tests`。结果 **763 passed, 1 warning, 217.01s**；日志 `D:/Cache/QQDigestReminders-2026-10-05/final-pytest.log`。现有 warning 为 Starlette TestClient 的 httpx 弃用提示。

定向回归覆盖实际 SQLite 来源、任务改期／完成、重新启用／命名修改基线、200 条游标余量、最近预览窗口、备份、旧故障超过 100 条、重试及未知结果、群范围迟到、过期列表／预览、保存冻结、鉴权／禁用／revision、并发读、共享连接回滚和请求取消锁。独立需求复审及代码质量复审均通过，报告指出的具体问题已修复并复跑。

静态验证：72 Python 模块 AST、34 Jinja 模板解析通过，`git diff --check` 通过。Windows x64 原生结构大小 976 字节、指针偏移核对通过；隐藏窗口、图标注册／版本和清理实际调用成功；本机合成通知请求返回接受。记录 `D:/Cache/QQDigestReminders-2026-10-05/native-verification.json`。

真实浏览器在 1280／390 宽度验证四类规则的预览与保存、指定群保留、25 条提醒的两页无交集、已读刷新后保留、重复检查新增 0 条、窄屏对话框及无控制台错误；设置页分类切换保留未保存的普通文本路径。密码输入在浏览器只读检测中被脱敏，不用其返回值判断草稿丢失。截图：`D:/Cache/QQDigestReminders-2026-10-05/browser/reminders-desktop.jpg`、`reminders-mobile.jpg`、`reminders-exe.jpg`、`settings-draft-retained.jpg`。

桌面包 `D:/Apps/QQDigestDesktop-2026-10-05-Reminders/QQDigestDesktop.exe` 已构建，34 模板与源码逐字节一致，4 新模块在 PYZ 中。使用独立合成配置实际启动 EXE，通过 health／版本／登录、提醒检测／去重／已读、Windows 能力接口及打包页面。记录 `D:/Cache/QQDigestReminders-2026-10-05/exe/exe-verification.json`，日志 `exe/verify.log`。测试进程已停止，launcher 已还原 `D:/Desktop/AI/chatbot/config/config.yaml`。

本轮验收未访问真实 QQ 数据，不调用 AI，不向他人发送通知。验证数据和日志全部位于 `D:/Cache/QQDigestReminders-2026-10-05`；已有项目数据和旧发布包保留。工作保存在本地分支 `codex/reminder-rules-2026-10-05`。
