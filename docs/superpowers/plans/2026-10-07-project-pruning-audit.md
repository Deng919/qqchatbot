# 项目冗余检查与确定性清理计划

**Goal:** 检查现有功能价值和代码引用，清理确定未使用的代码，给出功能收敛建议。

**Architecture:** 不改变现有页面、数据、配置和用户已保存的功能选择。删除生产路径中零引用的通用重试及过时快照实现，旧快照的测试改为覆盖当前主库与 WAL 实现；其余功能取舍记录为建议。

**Tech Stack:** Python／AST、FastAPI、SQLite、Jinja、pytest、Node。

## 检查与清理

- [x] 清点所有生产 Python、页面模板、HTTP／WebSocket 路由和功能开关；比对实际调用与产品边界。
- [x] 用独立合成数据检查 19 个受保护页面的未登录跳转和登录后渲染，解析所有 45 个模板；执行 20 个 Node 行为脚本。
- [x] 完成修改前的全量 pytest。
- [x] `qq_digest/pipeline.py`：删除无任何调用的 `with_retry` 和仅被该函数使用的 `time` 导入。
- [x] `qq_digest/refresh.py`：删除仅被两项旧测试调用的 `_copy_stable_snapshot`；`tests/test_refresh.py` 两项源文件变化测试改用生产中实际调用的 `_copy_database_snapshot`，保留变化重试、持续变化失败与清除不完整目标断言。
- [x] 删除未使用的导入：`web/app.py` 的 sqlite3／JSONResponse／load_config、`web/features.py` 的 Request、`desktop_update_worker.py` 的 STATE_DIR、`collector/ntqq.py` 的 timezone、`notify/notifier.py` 的 json／Path。保留 `notify/__init__.py` 公共导出和 `web/operations.py` 兼容转发。
- [x] 运行 `D:/CodexTools/python/Scripts/python.exe -X utf8 -m pytest -o addopts='' -q tests/test_refresh.py tests/test_pipeline.py tests/test_ntqq_collector.py tests/test_notification_queue.py tests/test_bot_commands.py tests/test_websocket.py tests/test_web.py tests/test_features.py tests/test_desktop_updates.py tests/test_audit_boundaries.py`，复查引用、AST 和 diff。
- [x] 写入 `docs/PROJECT_REVIEW_2026-10-07.md`，逐项说明保留／合并／可选／清理建议与证据；更新路线图优先级说明。

## 本轮交付边界

检查及清理只更新源码与文档。已有 SummaryNav 桌面程序、公开安装包、用户配置和数据保持当前状态。旧页面归并、收藏／待办边界调整及入口布局是下一轮具体产品修改，不能把这些建议写成已经完成。
