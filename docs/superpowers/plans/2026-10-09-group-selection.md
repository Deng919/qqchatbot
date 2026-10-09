# Unified Group Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with independent file ownership and review; steps use checkbox tracking.

**Goal:** 所有适合多群范围的既有入口支持一致的选群及批量操作。

**Architecture:** 共用 Python 范围规范化和原生网页选择组件。查询在后台过滤后分页；批量写入路由共享现有操作协调器、guarded_to_thread 和存储屏障。独立文件由并行任务负责，app.py 与网页由主代理整合。

**Tech Stack:** Python/FastAPI/SQLite/Jinja、原生 JavaScript、pytest、Node、Playwright。

## Task 1: 群范围及查询服务

Files: 新建 qq_digest/group_selection.py；修改 search.py、archive_qa.py、knowledge_library.py、candidates.py、topic_tracking.py 和对应 web 路由（app.py 由主代理整合）；新增 tests/test_group_selection.py、tests/test_multi_group_queries.py。

- [x] RED: 为 3 群合成归档查询 [1,3]，验证排除 2、total/分页、问答 scope/ticket，以及 [] 与单/多参数冲突拒绝。运行 `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_group_selection.py tests/test_multi_group_queries.py -q`，预期新增用例失败。
- [x] GREEN: 共用 `normalize_group_selection(group_id=None, group_ids=None, *, required=False)` 返回去重 tuple，None 表示全部、显式空集合拒绝。各服务新增 group_ids 并以参数化 IN 限定；保留旧参数。后台问答 scope 保留 group_id 并新增群集合。
- [x] 验证查询及原搜索、问答、知识、候选、话题回归测试全部通过。

## Task 2: 批量写操作

Files: 新建 qq_digest/group_batch.py、web/group_batch_routes.py、tests/test_group_batch.py；修改 history_inspection.py、web/history_inspection.py、web/attachment_routes.py。app.py 注册由主代理完成。

- [x] RED: 验证 POST /api/groups/batch/add、/api/groups/batch/settings、/api/collect/batch、/api/groups/batch/attachments/read 的选择校验、部分失败结果、一次刷新和锁。使用合成源库和替身刷新，不操作真实 QQ。
- [x] GREEN: 请求体操作群集合统一 group_ids；更新 `start,end,refresh`；设置 `patch`；添加 `groups:[{group_id,name}]`。结果包含 `results:[{group_id,status,error,...}]` 和总计。批量 collect 支持 dry_run 缺口检查；逐群事务。附件总预算沿用现有单次限额。所有 worker 使用 guarded_to_thread，锁在线程内部。
- [x] 历史巡检 run 新增可选 group_ids，保留定时全群语义与未参与群快照。运行相关测试。

## Task 3: 统一网页选群

Files: 新建 web/templates/group_selector.html、tests/group_selector.cjs；修改 base.html、collect.html、groups.html、group_details.html、search.html/find_script.html、archive_qa.html/archive_qa_script.html、summary_reading_controls.html/summary_reading_script.html、knowledge.html/knowledge_script.html、candidates.html、topics.html/topics_script.html、reports.html/summary_generation_script.html、first_use.html/first_use_script.html、reminders.html/reminders_script.html。

- [x] RED: Node 验证搜索保留选择、空指定范围阻断、选择筛选结果、disabled、替换群列表不丢已选、单/多链接序列化。`node tests/group_selector.cjs` 预期缺组件失败。
- [x] GREEN: 全局 `GroupSelector.mount(host,{allowAll,groups,selected,onChange})` 提供 value/setGroups/setSelected/appendParams/payload/label/setDisabled。用安全 DOM 节点输出，显式全部/指定模式。既有隐藏 select 保留作为单群兼容桥，不参与用户操作。
- [x] 接入各筛选范围及问答失效；群管理勾选集合串联更新/设置/附件读取/添加。批量结果冻结实际提交集合；只重试失败群。
- [x] 摘要生成和首次设置复用组件；提醒迁移前保留已有全群与多群语义。

## Task 4: 整合、复审和部署

- [x] app.py 注册批量路由；候选 API 接入群集合；同步阅读旧范围参数兼容。
- [x] 相关 Python 和全部 Node 脚本通过；独立先规格、再代码质量复审并修复。
- [x] 用 D:\Cache 下合成环境检查宽窄屏、旧链接、批量失败重试、筛选及问答；无真实采集/通知/AI调用。
- [x] 全量 pytest、模板解析、diff检查；记录真实结果。更新路线图与验收文件，提交本分支。
- [x] 按现有本机打包流程部署到 D:\Apps\QQDigestDesktop-2026-10-09-Groups，保留上一版，核对包内容并更新桌面快捷方式。不发布到 GitHub。
