# 查找体验 Implementation Plan

> 按批准的设计逐项实施，使用 TDD、真实浏览器验证及独立代码审查。

**Goal:** 消息入口改为查找，默认入口、清楚结果和原文侧边面板。

**Architecture:** 保留 FastAPI/Jinja/原生 JS，抽离页面样式、纯辅助函数和交互脚本，补充已有搜索结果字段。

**Tech Stack:** Python、Jinja、Node VM、Pytest、cua_repl。

## 任务

- [x] 测试先行：`tests/test_find_experience.py` 验证搜索时间/发送者、页面入口与四导航；`tests/find_helpers.cjs` 验证高亮转义、短时匹配合并和历史容错。运行定向 pytest，观察缺失行为失败。
- [x] `qq_digest/search.py` 为 message 结果增加 `timestamp` 与 `sender_qq`；页面拆为 `find_style.html`、`find_helpers.html`、`find_script.html`。输入不足两个字符只显示提示，不请求搜索或消息接口。
- [x] 默认入口、六条最近搜索与清空、显式类型按钮、折叠群/日期筛选。提交快照用于结果标题和高亮，旧请求返回不替换新结果。
- [x] 安全关键词高亮和本页短时匹配合并，保留逐条上下文入口及分页。面板异步竞态保护、错误重试、Escape 关闭、焦点返回和键盘焦点约束。
- [x] 全量 pytest、合成数据浏览器桌面/窄屏验证，独立审查后修复具体问题。
- [x] 打包 `D:\Apps\QQDigestDesktop-2026-10-04-Find`，核对模板与模块、共享配置及静默桥接，更新 `D:\Desktop\QQ Digest.lnk`。
- [x] 更新 ROADMAP，提交合并并推送；验证材料 `D:\Cache\QQDigestDesktop\find-qa-2026-10-04`。不启动此前被审批拦截的 EXE。


## 2026-10-04 完成与验证

- 完整测试：594 passed，110.03 秒；一条已有 Starlette/httpx 测试依赖弃用警告。
- TDD：先观察消息字段、页面入口和辅助模块缺失失败；之后增加并复现滚动恢复、日期校验取消加载、群列表失败不能扩大 URL 搜索范围、迟到初始化不得提交草稿四个回归，修复后通过。
- 真实合成数据浏览器：1280×900 与 390×844；空输入/单字、关键词高亮、长消息折叠、五分钟内分组、群筛选与实际结果范围、摘要类型切换、两页结果、最近搜索重开和清空、原文面板及 Escape/焦点恢复均验证。窄屏无横向溢出，HTML 模拟片段只呈现文本，控制台无错误或警告。
- 独立审查与复审完成，无剩余阻断问题；未访问真实消息、账户或调用 AI。
- 发布 `D:\Apps\QQDigestDesktop-2026-10-04-Find`：25 个模板哈希与源文件一致，业务/路由模块存在，搜索时间字段、后台标识和隐藏桥接标志正确。共享 launcher 配置保留，原关闭的开机自启保持关闭。
- `D:\Desktop\QQ Digest.lnk` 已指向新版，目标存在。实际 EXE 启动此前被自动审批审核拒绝，本轮未绕过，真实窗口启动仍未验证。
- 验证材料 `D:\Cache\QQDigestDesktop\find-qa-2026-10-04`：full-tests.log、build.log、find-home-desktop.jpg、find-home-mobile.jpg、find-context-desktop.jpg、find-context-mobile.jpg。合成 QA 服务已关闭。
