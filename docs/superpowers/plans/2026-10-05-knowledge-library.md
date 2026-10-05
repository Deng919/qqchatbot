# 知识库入口与原消息保存 Implementation Plan

> **For agentic workers:** 使用 executing-plans 在当前会话逐项执行，独立请求代码复审。

**Goal:** 已有知识始终可浏览，人工选择的原消息能直接入库并回查来源。

**Architecture:** 复用候选及知识索引、Markdown 存储。新增独立业务服务、认证路由和页面；保存表单供原消息组件复用。审核功能开关不控制知识库读取和人工保存。

**Tech Stack:** Python、SQLite、FastAPI、Jinja、原生 JavaScript。

## 1. 数据链路

- [x] 编写 tests/test_knowledge_library.py：关闭审核读取已确认知识、拒绝待确认详情、人工保存全文与来源、同群消息去重、跨群校验、非法输入、写入失败回滚；运行 pytest 验证缺失接口失败。
- [x] 新增 qq_digest/knowledge_library.py：list_items(q, group_id, item_type, page)、detail(candidate_id)、save_message(group_id, msg_id, title, item_type, reason, link)、confirm_candidate(candidate_id)。只查询实际入库且 confirmed 的记录；SQL 参数化，分页 20。保存先读真实原文，在单次 transaction 中创建/确认候选和记录索引，文件失败恢复原字节。
- [x] 新增 qq_digest/web/knowledge_routes.py：GET /knowledge、GET /api/knowledge、GET /api/knowledge/{id}、GET /api/knowledge/{id}/sources/{msg_id}、GET/POST /api/knowledge/from-message；全部 require_login，来源 API 验证已入库及消息关联，保存使用 knowledge_mutation 操作锁。app.py 注册并复用确认服务。

## 2. 入口和交互

- [x] 新增 knowledge.html、knowledge_script.html、knowledge_save.html；列表卡片显示类型、标题、群、保存时间和摘要，详情显示全部正文和来源，提供返回列表，过期响应不覆盖新导航。
- [x] section_navigation.html 在查找添加关键词查找/知识库；更多工具始终保留知识库，候选整理按原开关。search.py 和 find_script.html 将知识详情链接改为 /knowledge?item_id=，移除审核关闭时的阅读限制。
- [x] message_context_script.html 为指定 groupId 的原消息加入保存按钮；find_script.html、summary_reading_script.html、reports.html 传入真实群号。共用表单加载原文预填标题，保存期间禁止重复提交、保留失败输入、成功提供知识详情入口。
- [x] 前端 Node 回归检查过期详情和表单响应、保存失败重试、重复点击状态。

## 3. 交付

- [x] 独立复审并修复实际缺陷，全量 pytest -o addopts='' -q，核对旧审核开关和已有数据兼容。
- [x] scripts/build_desktop.py 加入新模板；后端标识更新，打包至 D:\Apps\QQDigestDesktop-2026-10-05-Knowledge；验证缓存 D:\Cache\QQDigestDesktop\knowledge-library-qa-2026-10-05。
- [x] 核对模板、模块、共享启动配置与偏好，更新 D:\Desktop\QQ Digest.lnk，记录 ROADMAP、提交并推送仓库。实际 EXE 启动沿用先前审批限制。

## 验证结果与限制

- 全量 634 项通过，相关知识／导航／操作锁／桌面测试 39 项通过。新接口最初 8 项因缺失失败；复审发现的旧 AI 候选复用正文、未入库搜索命中、预览覆盖标题分别先复现失败再修复。最终独立复审无阻塞项。
- 30 个模板哈希、10 个知识／摘要相关模块、后端标识 2026-10-05-knowledge、隐藏桥接标志均核对。共享启动配置、偏好保留，开机自启保持关闭；D:\Desktop\QQ Digest.lnk 指向新发布。
- [ ] 真实浏览器／EXE 运行验证：创建并启动合成验证服务的命令被自动审批拒绝，仅返回策略拦截；随后离线布局预览也因 file 协议不在浏览器允许列表中被安全策略拒绝。没有重试启动服务或绕过浏览器策略。接口及 Node 状态验证已完成，但不声称完成真实浏览器流程。
- 已生成的离线合成布局文件（未在浏览器打开）位于 D:\Cache\QQDigestDesktop\knowledge-library-qa-2026-10-05\knowledge-preview.html；同目录保留 build.log 和 verify_release.py。所有验证数据均为合成，未批准或修改本机待确认候选。
