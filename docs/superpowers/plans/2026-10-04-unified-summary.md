# 统一摘要 Implementation Plan

> **For agentic workers:** 使用 executing-plans 在当前会话逐项实现；完成后按 requesting-code-review 复审。

**Goal:** 用户通过日期生成和阅读摘要，不再需要分辨日报与范围摘要。

**Architecture:** 新生成路由复用已有手动业务，按单日/多日决定存储类型；共享单日记录与指纹。核心阅读在既有补看服务中启用多日报告，旧 API 保持兼容。

**Tech Stack:** Python、SQLite、FastAPI、Jinja、原生 JS、PyInstaller。

---

### Task 1：统一生成业务及路由

修改 `qq_digest/manual_summary.py`、`qq_digest/web/summary_generation_routes.py`、`qq_digest/web/app.py`；新测试 `tests/test_unified_summary.py`。

- [ ] 红：真实服务生成单日后运行自动任务，AI 调用只一次、同日期只有一条记录；反向复用同一键，滚动窗不复用，新增消息产生新版本，零点边界排除。
  ```python
  result = service.run(request)
  assert result.created_reports[0].report_key.startswith('daily:')
  assert archive.connection.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0] == 0
  ```
- [ ] 绿：新增 `single_day_as_daily` 选项；单日读取/写入 daily、复用有效输入，时间窗验证，手动 payload 明确半开边界。新生成路由使用同一 operations 互斥、鉴权和七日验证。
- [ ] 验证：`D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -q tests/test_unified_summary.py tests/test_manual_summary.py tests/test_pipeline.py`。

### Task 2：统一要点阅读

修改 `qq_digest/catchup.py`、`qq_digest/web/summary_reading_routes.py`、阅读模板和 `tests/test_summary_reading.py`。

- [ ] 红：多日报告可读、类型 ID 碰撞不共享已读状态、同群与日期筛选生效，旧 daily 键保持一致。
  ```python
  result = client.get('/api/summary-reading', params=scope).json()
  assert any(item['report_kind']=='range' for item in result['items'])
  ```
- [ ] 绿：core 调用增加 include_ranges，查询统一投影类型、日期窗；范围要点保留完整日期范围，来源和详情传实际类型；默认 legacy 补看行为不变。
- [ ] 验证：`python -X utf8 -m pytest -q tests/test_summary_reading.py tests/test_catchup.py tests/test_report_sources.py`。

### Task 3：日期驱动的生成界面

修改 reports、summary_generation_script、summary_reading_controls/script/style、设置/群聊文字，以及 `tests/summary_generation.cjs`。

- [ ] 红：一个按钮、三种时间选项，今天/七天同步日期，自定义保留所选范围；提交 `/api/summaries`，在途保护及失败后恢复。
  ```js
  setRangeDates('today');
  assert.equal(node('range-start-date').value,node('range-end-date').value);
  assert.equal(requests.at(-1).url,'/api/summaries');
  ```
- [ ] 绿：移除摘要页独立日报动作，侧栏统一标题；成功跳要点阅读、类型全部；日常设置显示每天自动生成摘要。
- [ ] 验证：`python -X utf8 -m pytest -q tests/test_summary_generation_ui.py tests/test_summary_reading.py tests/test_web.py`。

### Task 4：交付

- [ ] 合成预览验证宽窄窗口，生成今天/七天/自定义、已读及来源；独立代码复审并修复。
- [ ] 全量 `python -X utf8 -m pytest -q`、`git diff --check`。
- [ ] 发布 `D:\Apps\QQDigestDesktop-2026-10-04-Summary`；缓存材料 `D:\Cache\QQDigestDesktop\unified-summary-qa-2026-10-04`；核对模板/模块/共享配置，更新桌面入口。实际 EXE 启动此前被自动审批拒绝，不绕过。
- [ ] 更新路线图、提交、合并与推送仓库。
