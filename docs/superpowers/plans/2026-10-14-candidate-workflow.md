# 待确认知识处理流程修复计划

> 沿用用户已确认的当前会话自主执行方式，按 executing-plans 逐项完成。

**Goal:** 修复审查 13、14、15，明确编辑与入库语义，保持候选筛选和详情生命周期一致。

**Architecture:** “编辑后入库”改为“编辑候选”，保存仍只修改候选，详情新增独立“入库”。沿用既有入库与状态处理，三个动作同时支持 JSON 响应与旧 HTML 跳转；不新建组合保存／入库事务。前端捕获候选 ID 和详情 token，用 per-ID 在途集合阻止重复操作；列表用请求 token 忽略旧响应。错误原地显示，可重试。

**Tech Stack:** FastAPI、原生 JS/Jinja、pytest、Node VM、独立 Playwright Chromium。

备选方案：保存后自动入库需要处理两个动作间的失败和用户意图；新增原子编辑入库 API 会扩大当前服务契约。本轮选择语义明确的分步操作，复用现有可靠发布流程。不会实现候选分页（16），不会修改隐私策略（P2-07）或自动发送正式数据。

- [x] 在 `tests/test_web.py` 增加三个 JSON 动作的鉴权、状态、入库内容、旧 HTML 303 兼容、忙／文件失败／不存在及关闭 review 的回归，先跑失败。
- [x] 在 `qq_digest/web/app.py` 为 confirm/later/restore 增加 `/api/candidates/{id}/...` 别名和 JSON 响应，复用原有逻辑；later/restore 检查当前状态并用已有变更锁，明确 404/409。
- [x] 新建 `tests/candidates_ui.cjs` 执行真实模板：入口准确命名、关闭后保存成功不误报／重开、换目标后不覆盖、重复提交阻止、状态操作保持当前筛选、错误原地重试、旧列表响应忽略。先跑失败。
- [x] 修改 `qq_digest/web/templates/candidates.html`：按钮替换 HTML 状态表单，固定目标 ID／token，保持筛选和当前目标；使用现有 toast 加持久状态区，busy 控件在成功／失败后恢复。
- [x] CJS 全量和相关 Python 回归；独立复核后补必要边界。
- [x] 独立浏览器使用 `D:\Cache\QQDigestCandidateWorkflow-20261014` 合成数据检查原地操作、入库内容、保存关闭／换目标及宽窄屏，不使用 Codex 内置浏览器关闭流程。
- [x] 全量 Python 完整日志确认；更新服务标识，构建隔离 EXE 核对健康／版本／50 份模板／清单。
- [x] 更新路线图和审查状态，合并主分支并将桌面入口切到 `D:\Apps\QQDigestDesktop-2026-10-14-CandidateWorkflow-Release`，原版本保留回退。
