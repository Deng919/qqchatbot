# codexID 文本 ChatGPT Bridge 设计

## 目标

QQ 群摘要在本机运行时优先使用 ChatGPT 订阅账号。账号由
`D:\CodexTools\codexID` 的 Cockpit TXT/JSON 账号池提供；GPT 不可用时自动回退到
现有 OpenAI 兼容接口，保证每日摘要不会因单一供应方失败而中断。

## 约束

- 不修改 `chatgpt-bridge` npm 包，避免升级覆盖。
- 不在 Python 进程中解析、保存或记录 OAuth 令牌。
- 复用生图工具现有的账号校验、去重、互斥锁、临时认证文件、令牌刷新回写和日志脱敏逻辑。
- 每次文本调用最多尝试两个不同账号；账号池整体失败后才进入兼容接口回退。
- 临时 `auth-*.json` 仅当前用户可读，用完立即删除。

## 架构

### 工具层

在 `D:\CodexTools\gpttoimage-session` 增加文本调用能力：

- `CockpitImage.psm1` 增加调用 `chatgpt-bridge chat --json --no-stream` 的底层函数，并继续负责临时认证文件和刷新令牌回写。
- `CockpitPool.psm1` 增加文本账号池调度，复用现有账号快照、去重和单账号互斥锁。
- `chat-current-account.ps1` 提供稳定命令行接口。输入为 UTF-8 JSON 文件，输出为只含 `ok/content` 等非敏感字段的 JSON。

### 项目层

- `BridgeAIClient` 将摘要的 system/user 消息写入临时请求文件并调用包装器。
- `FallbackAIClient` 先调用 GPT，发生 `AIError` 时再调用原 `AIClient`。
- `build_ai_client()` 集中构造客户端，CLI、Web 和调度流水线不再重复拼装 provider。
- `ai.provider_priority` 默认是 `chatgpt_bridge,compatible`；服务器可改为只使用 `compatible`。

## 数据流

1. 摘要器提交消息列表。
2. Python 写入不含账号凭据的临时 JSON 请求。
3. PowerShell 账号池从 `codexID` 选择一个空闲有效账号并加锁。
4. 工具生成受限临时认证文件，设置 `CHATGPT_BRIDGE_AUTH_FILE` 后调用 bridge。
5. bridge 返回文本；工具刷新账号条目、删除临时认证文件并释放锁。
6. Python 解析摘要 JSON。bridge 失败时调用现有兼容接口。

## 错误与可观察性

- 包装器 stderr 和 Python 异常都不得包含令牌、邮箱、账号 ID 或源文件路径。
- GPT 成功、GPT 回退、全部失败分别记录 provider 名称和非敏感原因。
- 健康检查只报告账号文件数、有效账号数和 bridge 可执行状态。

## 验收标准

- Dry-run 能从 `codexID` 发现至少一个有效账号且不发起网络请求。
- 伪 bridge 测试证明选择的账号通过 `CHATGPT_BRIDGE_AUTH_FILE` 传入，并在调用后清理。
- GPT 成功时不调用兼容接口；GPT 失败时自动回退。
- 全量测试通过，真实服务重启后仍能采集和打开 Web UI。
