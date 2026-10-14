# 全功能审查修复实施计划

> **For agentic workers:** 使用 dispatching-parallel-agents 处理互不重叠文件的功能域；执行 TDD、规格审查、代码审查和 verification-before-completion。用户已选择当前会话执行，无需再次询问。

**Goal:** 完成17项新缺陷、12项旧缺陷及来源兼容端点修复，保留所有既有功能。

**Architecture:** 保留当前服务边界。独立数据库连接和revision解决并发；现有知识服务和可回滚文件安装解决发布；页面局部状态与精确范围解决交互。共享 app.py、models.py 由根代理负责。

**Tech Stack:** Python/FastAPI/SQLite，Jinja/原生JavaScript，pytest/CJS，隔离Playwright。

工作区：`C:\Users\xun\.codex\worktrees\interaction-drafts\chatbot`。证据根：`D:\Cache\QQDigestRemediation-20261014`。Python：`D:\CodexTools\python\Scripts\python.exe`。

## 1. 存储与桌面（独立文件）

Files: desktop.py, desktop_settings.py, desktop_updates.py, backup_restore.py, first_use.py, templates/first_use_script.html，以及对应测试；settings.html由根代理处理。

- [ ] F07 空secret、F08相对文件、F09二次中断、F13junction、F14发布前校验、F15空源路径。
- [ ] 旧11导入范围绑定；旧12由根代理启用备份目录按钮。
- [ ] 新回归先失败：桌面空密钥启动拒绝；自定义notes迁移内容保留；保存恢复中间状态后下一运行仍回退；真实junction源拒绝；过短secret不占目标；空db_dir更新prepare正常。
- [ ] 最小实现：复用路径检查、配置解析，复制前校验源树，staging内校验secret，保存recovering而非waiting；日期改变使向导完成项失效。
- [ ] 关联命令：`python -m pytest tests/test_desktop.py tests/test_desktop_settings.py tests/test_desktop_updates.py tests/test_backup_restore.py tests/test_first_use.py tests/test_first_use_web.py -q`。

## 2. 采集与发布（独立文件）

Files: cli.py, refresh.py, sync.py, collector/protobuf.py, notify/commands.py；相关采集服务。collect.html由根代理处理。

- [ ] F05完整游标、F06刷新回滚、F10/F11知识统一服务、F12多元素。
- [ ] 旧08已有库正常读取；旧09默认范围由根代理处理。
- [ ] 新回归先失败：停机7天后CLI与periodic都保留全部消息；第二rename失败旧集合完整；已确认候选later/ignore拒绝且文件一致；Bot元数据失败文件回滚；多元素正文合并。
- [ ] 实现复用run_message_sync、KnowledgeLibrary；多文件replace先保留旧目标并在异常回滚；无QQ号的已有源只读而不强制刷新。
- [ ] 关联命令：`python -m pytest tests/test_sync.py tests/test_refresh.py tests/test_bot_commands.py tests/test_protobuf.py tests/test_application_runner.py -q`。

## 3. 内容服务（独立文件，根代理负责app及models）

Files: archive_schema.py, archive.py, candidates.py, web/task_routes.py, templates/tasks.html, web/topic_routes.py, templates/topics_script.html, reminders.py；对应测试。不得修改app.py、models.py、candidates.html。

- [ ] F01worker连接、F02人工编辑保护、F03候选revision服务、F16正式草稿。
- [ ] 旧16候选分页服务、旧24先读话题、旧25精确来源链接。
- [ ] 新回归先失败：并发同建议只有1任务；重生成保留人工候选（同标题／改标题）；过期revision拒绝；修改另卡不丢draft；201条可查下一页；refresh失败已有话题可读；提醒链接携带消息／候选／任务标识。
- [ ] 数据迁移兼容旧archive；服务编辑返回冲突，根代理串接API；保留既有query供Bot，新增分页元数据不得破坏旧consumer。
- [ ] 关联命令：`python -m pytest tests/test_candidates.py tests/test_task_inbox.py tests/test_topic_tracking.py tests/test_reminders.py -q`与相关CJS。

## 4. Web整合（根代理）

Files: web/app.py, models.py, templates/candidates.html, group_details.html, groups.html, collect.html, summary_reading_script.html, base.html, login.html, settings.html, attachment_controls.html；按需复用小型模态控制脚本。

- [ ] F03 API读取revision/PATCH原子校验；F04群revision及变更字段；F17严格群schema/404。
- [ ] 旧07群浏览、09水位范围、12备份按钮、16分页UI、22精确成功报告、27停用附件文案、28modal隔离、29登录draft/返回地址，来源端点读取快照。
- [ ] 新回归先失败：stale PATCH返回409有效值保留；非法category不入库；page2有剩余候选；message入口有内容；本次生成不含旧报告；Tab不逃出modal；401后按原地址恢复草稿且不自动提交。
- [ ] 使用已存在group字段选择、report keys和版本状态；避免新的通用表单框架。
- [ ] 关联命令：`python -m pytest tests/test_web.py tests/test_message_browsing.py tests/test_report_qa_integrity_web.py -q`；`node --test tests/*.cjs`。

## 5. 集成、审查、交付

- [ ] 按域先规格符合审查，再代码质量审查；修复所有重要反馈。
- [ ] 全量pytest、CJS及50次页面冒烟；执行新增关键操作与失败恢复浏览器回归。
- [ ] 更新审查状态、路线图及验收文档，明确未测外部边界。
- [ ] 提交代码；按当前分支关系合入主工作区；必要时构建正式包并沿用已有发布/回退流程，不操作Codex自身进程。

核心验收示例：

```python
assert stale_edit.status_code == 409
assert candidate_after_regenerate.content == human_content
assert task_count_after_concurrent_confirm == 1
assert all(read_database(p) == old_values[p] for p in refresh_targets)
assert not failed_restore_destination.exists()
```

最终命令：`D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -o addopts= -q --tb=short`，`node --test tests/*.cjs`。真实外部链路未测则如实记录，不能靠模拟断言实机通过。
