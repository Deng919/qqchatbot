# QQ Digest 的 MyCortex 消息提交接口

状态：2026-11-19 用户确认手动和自动两种保存方式，已核对 MyCortex 接收契约。

## 目标

QQ Digest 提供一套提交接口，让摘要、收藏、已整理的资料和后续的单群看板都能把选出的有归档价值的 QQ 消息交给 MyCortex。QQ Digest 保有原始群聊和附件，MyCortex 保存提炼后的知识和必要的来源依据。

## 方案

- 旧 `export-mycortex` 仍适合离线交换，但当前只产出候选及上下文，缺少 MyCortex 现行导入入口要求的 `organization`。继续用旧格式批量转存会被新接收端拒绝。
- 推荐新增 QQ Digest 统一提交服务，通过 MyCortex 已有的 MCP `knowledge_write(action=submit)` 写入。使用显式配置的本机目标与独立的 `submit` 授权。客户端、内部模块均使用同一服务，不直接连接对方 SQLite，不新建接收协议。旧导出继续保留并明确其用途。
- 手动从条目点击「保存到 MyCortex」。自动按群开启，处理开启后生成且已经随摘要发布的候选，并且原消息也必须在开启后。两种方式经过同一筛选和提炼服务。自动默认关闭，不回填历史消息。

## QQ Digest 入口

建议在已登录的本机应用增加 `POST /api/mycortex/v1/submissions`。请求以一个群及一组原消息 ID 表示一条待整理知识。例如：

```json
{"group_id":12345,"message_ids":["msg-101","msg-103"]}
```

- 摘要、已确认候选、收藏、看板热点和资源从自身已有来源引用取得 ID。没有来源的条目提示补查原消息。各界面共享一个按钮和处理流程，未来看板只需接入这一入口。
- 后端校验所有消息属于该群，使用原始归档正文，限组内一到四十条。请求不携带数据库路径、任意文件、客户端编造的原文或 MyCortex 命令。原消息只在当前任务内处理，附件文件本身不随知识提交。
- Cockpit 根据选定消息筛选和提炼。手动保存也经过依据验证。提炼指令排除纯闲聊、秘密原值和不适合长期保留的内容。故障和证据不足显示对应状态。

## MyCortex 接收契约

按 `knowledge_write` 提交 `action`、稳定的 `requestId`、`title`、`sourceUri`、`author`、`observedAt`、临时依据 `body` 和 `organization`。`body` 只携带通过验证的引文。首条实际引用消息使用 `qqdigest://group/<群号>/message/<消息ID>`，涉及多条原消息时，以重复的 `message_id` 参数保存其余条目的来源 ID，顺序与知识条目一致。资料时间取首条引用，单一来源使用归档中的发送者显示名。

`organization` 使用 MyCortex 的现行结构。`topics` 最多五个。`entries` 最多八个，每项含摘要 `summary`、归属 `subject`、立场 `stance`、逐字引文 `quote`、可空的原话时间 `timeText`。只有引文能在对应原消息中逐字找到，且 `timeText` 确实来自该引文时才发出。用空结果表达没有可保存知识。

同一目标、同一组原消息对应一个稳定提交 ID。提交失败或回执丢失时复用同一请求，不静默换 ID 新建；同一来源已保存后显示回执，来源变化由明确的更新流程处理。知识已经在 MyCortex 删除或人工修正时，不通过重试自动重建或覆盖。收到真实 `saved` 或 `skipped` 回执才更新界面，连接不可用时明确显示待重试。

## 验证范围

使用合成的 QQ 归档及独立 MyCortex 库，验证同群引用、越界拒绝、空提炼、逐字引文校验、重复提交、网络失败后的重试和读取回执。检查 MyCortex 存储只包含提炼知识与选定引文。功能交付前核验相关 Web 入口、更新项目记忆、提交并推送适用工作分支。

## 依据

- 桌面 MyCortex `docs/superpowers/specs/2026-11-16-refined-knowledge-design.md` 的入库原则。
- `core/src/knowledge/organization-contract.ts` 和 `core/src/agents/tools/knowledge-tools.ts` 的字段及返回值。
- `core/src/knowledge/qqdigest-import.ts` 对旧 bundle 的提炼检查。
