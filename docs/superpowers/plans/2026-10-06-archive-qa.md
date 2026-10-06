# P2-02 归档问答实施计划

**Goal:** 指定群与日期的原消息问答和可点击来源。
**Architecture:** archive_qa.py 管理检索、不可变预览、票据及回答校验；web/archive_qa_routes.py 负责鉴权、工作线程与 DeepSeek 客户端；三个模板管理结构、样式和交互。
**Tech Stack:** SQLite FTS5、Python、FastAPI、原生 JS。

- [x] 先写 tests/test_archive_qa.py，验证范围、命中与未命中、引用、票据失效、并发和来源变更；运行确认失败。
- [x] 新建业务模块，查询绑定参数，使用每条独立 s 标识；限制上下文、缓存和期限，发送前复核指纹，同票据只生成一次。
- [x] 新建 API 和页面路由：POST preview、POST ask、GET sources/{ticket}/{source}；工作线程持有 archive_qa 操作锁并关闭客户端；配置错误映射为 503，不回显密钥或原始异常。
- [x] features.py 增加独立开关，默认关闭；operations.py 增加互斥类型；app.py 组装路由；查找次级导航常驻入口。
- [x] 新建模板：问题/群/日期/可选关键词；预览片段与发送按钮；过期响应保护；输入修改使旧预览失效；范围与来源在结果中展示。
- [x] 新增接口及 Node 交互回归，采用合成 AI；真实 HTTP 宽窄屏预览、回答、来源与关闭功能检查。
- [x] 独立审查、全量测试、更新路线图和验收记录、保存开发分支；本次不发布安装包。

验收结果：926 passed、1 skipped；独立复审、合成边界与实际 HTTP 宽窄屏检查通过。详见 [验收记录](../specs/2026-10-06-archive-qa-verification.md)。仅源码提交，未更新安装包。
