# Group attachments Implementation Plan

**Goal:** 按群开启图片与文件读取，自动用于摘要和问答，可查看和重读附件。

**Architecture:** 独立附件来源解析、本地格式读取、缓存服务和 API；SQLite 保存原消息对应的读取结果，所有使用入口按群开关过滤。日常采集和生成调用同一服务，原消息正文保持不变。

**Tech Stack:** Python、SQLite、FastAPI、Pillow/PyMuPDF、DeepSeek Chat Completions、原生 JavaScript。

- [x] TDD 验证来源映射、全文库附件补入、按群开关和迁移。
- [x] 实现格式读取、真实内容校验、缓存和错误/截断状态，使用模拟 AI 验证识图请求。
- [x] 接通自动采集、每日/范围/重生成及问答，附件内容可检索且关闭后不再参与。
- [x] 实现群设置开关、读取状态和重读；登录及并发保护，浏览器宽窄屏验收。
- [x] 全量回归、独立复审、文档更新和本机打包部署，不启用用户真实群。
