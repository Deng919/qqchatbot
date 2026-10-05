# P1-03 话题持续跟踪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 先修复项目检查确认的基础问题，再实现有来源、可纠正的跨天跨群话题跟踪。

**Architecture:** 本地摘要索引和话题关联由独立服务承担；网页路由只验证请求并调用服务。SQLite 保存话题、讨论、人工修改与修订号；现有报告与消息不被改写。继续在当前项目目录工作，使用 `codex/topic-tracking-2026-10-05` 分支，保留未跟踪用户文件。

**Tech Stack:** Python 3.11+、SQLite、FastAPI、Jinja2、原生 JavaScript、pytest、Node。

**Python:** `D:/CodexTools/python/Scripts/python.exe`。

## 1. 检查与基础修复

- [x] 现有全量测试：634 passed，131.40s；68 Python AST、30 Jinja 模板解析通过。
- [x] 独立复审并以合成数据复现采集游标、源库刷新、备份并发、搜索分页与 wheel 模板遗漏。
- [x] 新增回归：日报先运行后增量同步仍回填历史；刷新不会留下被优先读取的旧全文库；备份检测跨数据库/文件发布变化；同日 25 条知识搜索两页无交集且全部可达；wheel 包含模板。
- [x] 对应修改 `qq_digest/pipeline.py`、`qq_digest/refresh.py`、`qq_digest/backup_restore.py`、`qq_digest/search.py`、`pyproject.toml`。先运行新增测试并确认失败，再做最小修复。
- [x] 运行 `-m pytest tests/test_pipeline.py tests/test_sync.py tests/test_refresh.py tests/test_backup_restore.py tests/test_product_workflows.py`。
- [x] 独立复审修复的需求覆盖及质量，保存检查记录。

搜索排序必须在 SQL 截取和 Python 合并时使用相同类型顺序：候选及报告 ID 使用整数，消息 ID 使用字符串，类型排序保持原逻辑。不得把全部历史结果读入内存来绕过分页。

日报采集覆盖仅是报告窗口；不得把这段窗口作为全历史同步已完成。全文库选择必须与本次成功安装的源库集合一致。备份以数据库和文件一致性为标准，而非仅校验各文件哈希；跨时点变化失败并保留上次备份。

## 2. 本地话题服务与数据迁移

**Create:** `qq_digest/topic_tracking.py`、`tests/test_topic_tracking.py`。
**Modify:** `qq_digest/archive.py`（新表与群删除）、`qq_digest/features.py`。

- [x] RED：新增服务测试，首次调用下面的公共 API 因缺少功能失败；关联、幂等、人工修正、旧版和有效来源逐项用真实 SQLite 验证。
- [x] GREEN：实现公共 API，严格使用参数化 SQL，事务内保存修订变化：

```python
TopicTrackingService(archive, timezone_name='Asia/Shanghai')
service.refresh()  # {reports_processed, skipped_reports, discussions, topics}
service.list_topics(q='', group_id=None, date_from=None, date_to=None,
                    status='all', page=1, page_size=20)
service.detail(topic_id, page=1, page_size=20)
service.update(topic_id, title=None, status=None, expected_revision=0)
service.move(discussion_id, target_topic_id=None, new_title=None,
             expected_revision=0)
service.merge(topic_id, target_topic_id, expected_revision=0,
              target_revision=0)
service.suggestions(discussion_id)
service.source(discussion_id, msg_id, direction='around', cursor=None)
```

- [x] 相同规范化的具体标题自动关联；模糊匹配只给建议。来源按报告群和日期验证。结论与问题只有共享有效原消息才能归属。
- [x] 刷新采用每个报告的内容指纹与稳定讨论身份；人工所属话题不被覆盖。消失的讨论标旧版，旧版不参与最新进展；损坏报告保持旧记录并标明不可核实。
- [x] 话题支持重名、合并、拆出、关联、更名、归档及修订冲突；删除群的记录由外键级联清理，空话题默认不显示。
- [x] 迁移重复运行、备份恢复、Unicode/泛化标题、多群同 ID、报告改版和排序改变都有回归。
- [x] 运行 `-m pytest tests/test_topic_tracking.py tests/test_archive.py tests/test_features.py tests/test_backup_restore.py` 后独立复审。

## 3. 独立网页接口与功能开关

**Create:** `qq_digest/web/topic_routes.py`、`tests/test_topic_routes.py`。
**Modify:** `qq_digest/web/app.py`（组装）、`qq_digest/web/features.py`（守卫）、`qq_digest/web/operations.py`（互斥）。

- [x] RED：未登录接口 401、页面 303；关闭功能 403；旧修订 409；无目标 404；坏日期/超长标题/未知字段 422；任务占用时刷新/纠正 409。
- [x] GREEN：GET `/topics`、GET `/api/topics`、POST `/api/topics/refresh`、GET/PATCH `/api/topics/{id}`、POST `/api/topics/{id}/merge`、PATCH `/api/topic-discussions/{id}`、GET `/api/topic-discussions/{id}/suggestions`、GET `/api/topic-discussions/{id}/sources/{msg_id}`。
- [x] 更新使用独立 `topic_mutation` 操作类型，网页入口在摘要下；设置保存的精简预设不会意外开启话题。
- [x] 运行 `-m pytest tests/test_topic_routes.py tests/test_features.py tests/test_operations.py`。

## 4. 话题页面与交互

**Create:** `qq_digest/web/templates/topics.html`、`topics_script.html`、`tests/topic_tracking_ui.cjs`、`tests/test_topic_tracking_ui.py`。
**Modify:** `primary_navigation.html`、`section_navigation.html`、`scripts/build_desktop.py`。

- [x] 先建立交互回归：迟到详情不能覆盖当前话题；刷新与保存不能重复提交；失败保持编辑值；关闭来源销毁旧请求。
- [x] 列表组合筛选与分页；详情时间线、最新结论及问题、报告与原文按钮；改名、归档、合并、移动与拆出对话框；来源复用 `mountMessageContext`。
- [x] 复用样式，安全文本节点，不渲染用户 HTML；窄屏动作换行，键盘可操作，错误和等待明确展示。
- [x] 页面打开 POST 本地刷新后读列表；功能关闭不发起刷新。手动刷新失败保留列表并提供重试。
- [x] 设置动态工具入口同步开关；新增模板加入打包清单。
- [x] 运行 `-m pytest tests/test_topic_tracking_ui.py tests/test_primary_navigation.py tests/test_desktop.py`，真实浏览器验证 1280 与 390 宽度、合并/拆出/纠正/来源。

## 5. 验证、记录与交付

- [x] 全量 `-m pytest`、`git diff --check`、所有 Python/Jinja 解析；独立需求审查后质量审查，修复有效意见。
- [x] README 写清话题规则与来源限制；ROADMAP 只有验收通过才勾选 P1-03，记录日期与测试结果。
- [x] 检查记录存 `docs/superpowers/specs/2026-10-05-project-audit.md`；临时复现、浏览器数据和 wheel 只在 `D:/Cache/QQDigestAudit-2026-10-05`。
- [x] 如发布桌面版，输出 `D:/Apps/QQDigestDesktop-2026-10-05-Topics`，缓存 `D:/Cache/QQDigestDesktop`；模板核对、合成启动验证后交付。不能验证真实启动就明确记录。
- [x] 提交经过验证的项目变化；保留用户未跟踪文件，报告当前分支、修改路径及验证限制，不推送远程。


## 最终执行结果

2026-10-05：695 passed，1 个既有 httpx 弃用警告，163.40s；71 Python AST 与 32 Jinja 模板解析、git diff --check 通过。两轮独立需求及质量复审通过，版本标点、重复项身份、事务隔离、次级来源和过期目标提交边界均有回归。宽窄屏及列表／时间线分页通过。桌面包已发布并完成合成 EXE 后台启动和打包页面验收，launcher 已恢复项目配置，测试进程已关闭。详细范围和限制见 ../specs/2026-10-05-project-audit.md。
