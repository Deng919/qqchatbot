# Summary Progress Implementation Plan

> Execute inline with executing-plans and test-driven-development.

**Goal:** 显示真实的摘要生成进度、当前阶段及结果，并在刷新后恢复。

**Architecture:** ManualSummaryService 发出处理阶段，独立的线程安全进度容器维护 UUID 快照。生成路由关联快照；独立 UI 模块轮询状态。原同步 API 保持兼容。

**Tech Stack:** Python/FastAPI、threading.Lock、原生 HTML progress、JavaScript、pytest/Node。

### 1. 后端进度

- [x] 增加 tests/test_summary_progress.py：服务回调依次观察 scanning / summary / publication / created，复用、跳过、失败各计数一次；线程阻塞模拟 AI 时通过 GET 查询未完成进度，重复 UUID 不再次运行。
- [x] 使用 `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -q tests/test_summary_progress.py` 确认新增断言失败。
- [x] 新增 qq_digest/summary_progress.py：start/update/finish/snapshot，锁保护、20 次保留、时间与统计、UUID 标识，快照复制。
- [x] 修改 manual_summary.py 为 `run(request, *, on_progress=None)`；每群开始和阶段转换报告，完成/复用/跳过/失败更新终态。
- [x] app.py worker 传入回调；summary_generation_routes.py 生成 payload 可选 UUID、进度认证 GET、重复 ID 拒绝启动、终态保存结果。
- [x] 跑新增及现有统一摘要、手动摘要、操作协调测试。

### 2. 前端进度

- [x] 新增 Node 回归：真实快照 2/9、阶段、部分失败、串行轮询及刷新恢复；先观察失败。
- [x] 独立 summary_progress_script.html 负责轮询、双位置进度绘制、连接异常及恢复；summary_generation_script.html 提交 UUID，并共用结果渲染。
- [x] summary_reading_controls.html、reports.html 放置有标签的 progress 和统计文本；summary_reading_style.html 加进度布局。build_desktop.py 明确包含新增模板。
- [x] 运行 Node + pytest 前端测试；浏览器以模拟 AI 验证生成中、完成、刷新恢复、宽窄界面。

### 3. 交付

- [x] 完整测试及 diff 检查；更新迭代记录和桌面版本。
- [x] 构建到 D:\Apps\QQDigestDesktop-2026-10-05-Progress；验证归档模块/模板；保持启动器配置和偏好，桌面快捷入口更新。
- [x] 提交并同步仓库，报告验证范围与新版位置。

## 验证记录

- 全量 pytest：619 项，退出码 0。日志 D:\Cache\QQDigestDesktop\summary-progress-qa-2026-10-05\full-tests-final.log。
- 新增进度接口与真实服务阻塞测试、重复请求、重复取消锁、前端旧轮询覆盖终态／404／恢复日期测试均通过；独立复审无阻断项。
- 模拟 AI 浏览器验证 0/2 → 1/2 → 2/2、当前群、刷新恢复、重新打开面板，以及复用 2 份；375 像素内容宽度无横向溢出，控制台无错误。截图 progress-desktop.jpg 和 progress-mobile.jpg 在同一验证目录。
- 桌面构建退出码 0；27 个模板一致，8 个摘要相关模块、版本和无命令行桥接标记核对通过；沿用上一版启动器与偏好，开机启动保持关闭。发布 D:\Apps\QQDigestDesktop-2026-10-05-Progress；桌面 D:\Desktop\QQ Digest.lnk 指向新版。
- 实际 EXE 启动仍受此前自动审批拒绝限制，未重试。程序重启会清空内存进度；摘要记录仍保存在原有存储中。
