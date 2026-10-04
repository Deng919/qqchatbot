# 统一摘要 Implementation Plan

> **For agentic workers:** 使用 executing-plans 在当前会话逐项实现；完成后按 requesting-code-review 复审。

**Goal:** 用户通过日期生成和阅读摘要，不再需要分辨日报与范围摘要。

**Architecture:** 新生成路由复用已有手动业务，按单日/多日决定存储类型；共享单日记录与指纹。核心阅读在既有补看服务中启用多日报告，旧 API 保持兼容。

**Tech Stack:** Python、SQLite、FastAPI、Jinja、原生 JS、PyInstaller。

---

### Task 1：统一生成业务及路由

修改 `qq_digest/manual_summary.py`、`qq_digest/web/summary_generation_routes.py`、`qq_digest/web/app.py`；新测试 `tests/test_unified_summary.py`。

- [x] 红：真实服务生成单日后运行自动任务，AI 调用只一次、同日期只有一条记录；反向复用同一键，滚动窗不复用，新增消息产生新版本，零点边界排除。
  ```python
  result = service.run(request)
  assert result.created_reports[0].report_key.startswith('daily:')
  assert archive.connection.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0] == 0
  ```
- [x] 绿：新增 `single_day_as_daily` 选项；单日读取/写入 daily、复用有效输入，时间窗验证，手动 payload 明确半开边界。新生成路由使用同一 operations 互斥、鉴权和七日验证。
- [x] 验证：`D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -q tests/test_unified_summary.py tests/test_manual_summary.py tests/test_pipeline.py`。

### Task 2：统一要点阅读

修改 `qq_digest/catchup.py`、`qq_digest/web/summary_reading_routes.py`、阅读模板和 `tests/test_summary_reading.py`。

- [x] 红：多日报告可读、类型 ID 碰撞不共享已读状态、同群与日期筛选生效，旧 daily 键保持一致。
  ```python
  result = client.get('/api/summary-reading', params=scope).json()
  assert any(item['report_kind']=='range' for item in result['items'])
  ```
- [x] 绿：core 调用增加 include_ranges，查询统一投影类型、日期窗；范围要点保留完整日期范围，来源和详情传实际类型；默认 legacy 补看行为不变。
- [x] 验证：`python -X utf8 -m pytest -q tests/test_summary_reading.py tests/test_catchup.py tests/test_report_sources.py`。

### Task 3：日期驱动的生成界面

修改 reports、summary_generation_script、summary_reading_controls/script/style、设置/群聊文字，以及 `tests/summary_generation.cjs`。

- [x] 红：一个按钮、三种时间选项，今天/七天同步日期，自定义保留所选范围；提交 `/api/summaries`，在途保护及失败后恢复。
  ```js
  setRangeDates('today');
  assert.equal(node('range-start-date').value,node('range-end-date').value);
  assert.equal(requests.at(-1).url,'/api/summaries');
  ```
- [x] 绿：移除摘要页独立日报动作，侧栏统一标题；成功跳要点阅读、类型全部；日常设置显示每天自动生成摘要。
- [x] 验证：`python -X utf8 -m pytest -q tests/test_summary_generation_ui.py tests/test_summary_reading.py tests/test_web.py`。

### Task 4：交付

- [x] 合成预览验证宽窄窗口，生成今天/七天/自定义、已读及来源；独立代码复审并修复。
- [x] 全量 `python -X utf8 -m pytest -q`、`git diff --check`。
- [x] 发布 `D:\Apps\QQDigestDesktop-2026-10-04-Summary`；缓存材料 `D:\Cache\QQDigestDesktop\unified-summary-qa-2026-10-04`；核对模板/模块/共享配置，更新桌面入口。实际 EXE 启动此前被自动审批拒绝，不绕过。
- [x] 更新路线图、提交、合并与推送仓库。


## 完成与验证记录

- 614 项全量测试通过（退出码 0）；新增 8 项统一生成服务/接口回归、3 项阅读回归、1 项多群 JS 回归。首次全量发现旧标题断言需随统一文案更新，修正后全量通过。`git diff --check` 通过。
- 单日双向复用、归档新增后保留版本、午夜消息排除、任务互斥及鉴权通过；旧范围接口保持其历史行为，新统一入口才使用共享单日记录。
- 独立复审修复多日新增判断用错时间、多群阅读范围扩大、运行记录遗留另一生成流程；复核无剩余阻断。清理旧生成函数时发现的 dashboard 脚本尾段已移除，Node 解析与真实浏览器运行记录检查通过。
- 合成浏览器验证今天、七天、自定义双群生成和来源跳转；双群结果不包含第三个未选群，复用显示“生成 0 份，复用 2 份”。宽窄窗口无横向溢出，控制台无错误或警告；自动设置显示每天自动生成摘要。
- 发布包核对 26 个模板哈希、7 个摘要/阅读模块、后台标识 `2026-10-04-summary` 与静默桥接标志。共享 launcher 配置和原有偏好保留；开机自启原为关闭且未改变。未运行真实 EXE：此前自动审批审核拒绝启动，该限制未绕过。
- `D:\Desktop\QQ Digest.lnk` 指向 `D:\Apps\QQDigestDesktop-2026-10-04-Summary\QQDigestDesktop.exe`。截图与日志保存于 `D:\Cache\QQDigestDesktop\unified-summary-qa-2026-10-04`；合成预览服务已停止。
- 旧报告正文和独立记录不删除或迁移；本次统一新生成内容和交互，新报告格式版本为 8，旧格式首次更新可能重新生成一次。
