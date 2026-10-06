# P2-01 全文检索实施计划

**Goal:** 中文关键词索引与发送人、消息类型组合筛选。
**Architecture:** 消息索引独立模块，归档负责初始化；搜索业务使用索引候选后做字面校验，网页接入拆入 search_routes.py。
**Tech Stack:** Python、SQLite FTS5、FastAPI、原生 JS。

在当前 codex/public-beta-2026-10-06 分支继续，保留未提交摘要显示修改。按任务执行并验证；用户已要求继续实施，不另建工作区。

## 1. 索引与搜索业务

- [x] 新建 tests/test_message_search.py，覆盖旧库回填、中文两字、特殊字符、组合筛选、分页、索引触发器回滚及 SQLite 直接写入。
- [x] 运行 `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_message_search.py`，确认新筛选及索引行为失败。
- [x] 新建 qq_digest/message_search.py：`search_tokens(text)` 返回相邻字符的十六进制 token，用 FTS5 的 AND 查询找候选；`install_message_index(connection)` 在事务中创建 contentless 索引和 INSERT/UPDATE/DELETE 触发器及回填版本标记；只有缺少 FTS5 时返回 False。
- [x] Archive 初始化迁移之后保存 message_search_indexed 标志。search_archive 增加 sender_qq、message_type 参数及范围校验；在消息分支增加索引子查询和相同 COUNT/SELECT 筛选，保留转义 LIKE；返回消息类型。
- [x] 运行新测试及 search_pagination、archive、backup_restore 相关测试。

## 2. 网页接入与筛选

- [x] 在 tests/test_find_experience.py 增加接口组合条件、鉴权、参数校验和页面控件检查。
- [x] 新建 qq_digest/web/search_routes.py，通过 Depends(require_login) 接入 search_archive；移出 app.py 原搜索端点，保留原消息上下文端点及其内部调用。
- [x] search.html 增加消息筛选字段与类型选项；find_script.html 仅消息模式提交它们，结果范围基于提交值显示，刷新恢复，翻页沿用提交条件；find_style.html 增加独立筛选布局。
- [x] Node 交互测试覆盖模式切换与提交范围，相关 Python 接口测试通过。

## 3. 验证与记录

- [x] 在 D:\Cache\QQDigestFullTextSearch-2026-10-06 用合成数据核对大归档索引与 LIKE 结果及耗时，记录索引占用和初始化成本。
- [x] 使用实际 HTTP 测试服务检查宽窄屏页面、组合筛选、翻页、刷新、来源、高亮转义；不使用之前被浏览器拒绝的 file 页面。
- [x] 执行全量 pytest 与摘要显示 Node 回归，检查 diff 并审查数据兼容性。
- [x] 更新 docs/ROADMAP.md 和本计划，准确记录通过检查及限制；安装包仍保持已有公开版，除非另行发布。
