# QQ Digest

本地 QQ 群消息采集、每日摘要、重点信息审核和 Markdown 知识库工具。

## 已实现功能

- 从本机 QQ NT 的解密数据库发现群聊并读取消息。
- 首次回填最近 30 天，之后默认每 10 分钟自动刷新并增量采集。
- 按群分类生成自适应摘要：内容少时简洁，讨论丰富时自动扩展话题覆盖；默认使用 DeepSeek V4.1 Flash 的 OpenAI-compatible API。
- 将 Markdown 和 JSON 摘要写入 `reports/`。
- 将资源与经验先放入候选审核，确认后写入 `knowledge/resources.md` 或 `knowledge/experiences.md`。
- Web UI 提供运行总览、群组配置、手动采集、摘要查看和候选审核。
- Web 服务内置独立的周期采集和每日摘要调度，也提供 CLI 和手动执行入口。
- 候选支持按状态、群、类型、日期和关键词筛选，并可查看历史状态与详情。
- AI、日报和 QQ Bot 通知均有有界重试；通知队列持久化在 SQLite 中。

QQ 数据库格式是私有实现，QQ 更新后可能需要同步调整解析器。刷新过程先复制主库与 WAL 的一致快照，再解密并校验当前采集路径使用的核心数据库，最后成组替换输出；失败时不会覆盖上一份可用数据。新版 QQ 存在全文库时，采集依赖 `group_info.db` 和 `group_msg_fts.db`，历史 `nt_msg.db` 的内部损坏会告警但不会阻止最新消息刷新。

## 安装

本机默认使用 Codex Python 环境：

```powershell
D:\CodexTools\python\Scripts\python.exe -m pip install -e ".[test]"
```

复制配置并生成登录密码：

```powershell
Copy-Item config/config.example.yaml config/config.yaml
D:\CodexTools\python\Scripts\qq-digest.exe hash-password "你的密码"
```

将密码散列写入 `security.web_password_hash`，再设置会话密钥。可以使用环境变量：

```powershell
$env:QQ_DIGEST_SESSION_SECRET="一段随机长字符串"
```

也可以把至少 32 个字符、无空白的随机密钥保存到项目外文件，并配置 `security.session_secret_file`。环境变量的优先级更高。

默认直接调用 DeepSeek V4.1 Flash 的 OpenAI-compatible 接口。该模型在 DeepSeek API 中的模型 ID 为 `deepseek-flash`。JSON 输出模式用于降低摘要响应格式错误的概率。API key 优先从 `ai.api_key_env` 指定的环境变量读取，其次读取 `ai.api_key_file`；密钥不会写入日志或项目文件。

```yaml
ai:
  provider_priority: [compatible]
  base_url: https://api.deepseek.com
  model: deepseek-flash
  json_mode: true
  api_key_env: DEEPSEEK_API_KEY
```

若密钥保存在项目外的单行文本文件，可另设 `ai.api_key_file` 为该文件的绝对路径；不要把密钥写进配置或提交到 Git。

如需使用本机 `codexID` ChatGPT bridge，可在确认包装器和账号池可用后，将 `chatgpt_bridge` 显式加入 `provider_priority`。

知识库 Markdown 文件可使用相对或绝对路径：

```yaml
knowledge:
  resource_path: knowledge/resources.md
  experience_path: knowledge/experiences.md
```

相对路径以项目数据目录为基准。Web 审核和 QQ Bot 指令会写入同一目标文件，并在归档库记录对应路径。

## 配置真实 QQ 数据

在 `config/config.yaml` 中启用 NTQQ：

```yaml
ntqq:
  enabled: true
  qq_number: 你的QQ号
  db_dir: "D:/Cache/output/你的QQ号"
  timezone: Asia/Shanghai
```

先刷新数据库，再回填已选群：

```powershell
D:\CodexTools\python\Scripts\qq-digest.exe refresh --config-path config/config.yaml
D:\CodexTools\python\Scripts\qq-digest.exe backfill --config-path config/config.yaml --days 30
```

运行时群组配置以 `archive/archive.sqlite` 为准。`config.yaml` 的 `groups` 只做首次导入；在 Web UI 中添加、修改和删除的群组会直接持久化到归档库。
群组页可以分别配置重点关键词和首次采集天数。关键词会进入该群的摘要提示词，仅提高相关讨论的关注优先级，不会降低重点候选标准；首次采集天数只在该群尚无同步游标时生效。

## 启动

```powershell
D:\CodexTools\python\Scripts\qq-digest.exe serve --config-path config/config.yaml
```

如果未配置 `security.session_secret_file`，启动前仍需设置 `QQ_DIGEST_SESSION_SECRET` 环境变量。

默认地址为 `http://127.0.0.1:8765`。需要手机审核时，将 `web.host` 配为 `0.0.0.0`，在同一局域网内通过电脑 IP 访问；不要把该服务直接暴露到公网，远程访问优先使用 Tailscale 等私有网络。

Web 服务启动 15 秒后会执行第一次消息同步，之后按 `collection.interval_minutes` 自动导入。每次同步会向前重叠 `collection.overlap_minutes`，并通过消息 ID 去重，避免数据库快照延迟导致漏消息。QQ 未运行时任务会保留失败状态，并在下个周期重新检测。

摘要任务独立按 `summary.hour` 和 `summary.minute` 每日执行，也可在总览页手动生成。失败后默认每 15 分钟重试，最多尝试 3 次；服务重启后会从 `jobs` 表恢复当天状态。AI 请求默认对网络错误、408、429 和 5xx 做最多 3 次指数退避重试，其他 4xx 和响应格式错误立即失败。相关配置：

```yaml
summary:
  max_attempts: 3
  retry_interval_minutes: 15
ai:
  provider_priority: [compatible]
  base_url: https://api.deepseek.com
  model: deepseek-flash
  json_mode: true
  max_retries: 3
  retry_base_seconds: 2
qq_bot:
  max_retries: 3
  retry_base_seconds: 60
```

AI 服务失败不会阻止消息采集。QQ Bot 通知会先进入 `send_log` 持久队列，临时失败按指数间隔重试，达到上限后保留失败状态。CLI 入口：

```powershell
D:\CodexTools\python\Scripts\qq-digest.exe run-daily --config-path config/config.yaml
```

启用 QQ Bot 后，白名单用户可以通过私信审核候选：`候选`、`查看 12`、`入库 12 13`、`忽略 14 原因`、`稍后 15`。原有 `/list`、`/view`、`/confirm`、`/ignore`、`/later` 命令仍然兼容；批量入库逐条执行，个别 ID 失败不会撤销已经成功的条目。

## 检查与测试

```powershell
D:\CodexTools\python\Scripts\qq-digest.exe doctor --config-path config/config.yaml
D:\CodexTools\python\Scripts\python.exe -m pytest
```

## 安全边界

- 不以写模式打开 QQ 原始数据库。
- 数据库解密输出放在配置的 `ntqq.db_dir`，刷新失败保留上一份可用副本。
- API key、会话密钥和 QQ Bot 密钥不进入 Git。
- 只有经过登录的 Web 会话可以调用业务 API。
- AI 只接收经过长度限制的文本上下文，不上传媒体文件本体。
