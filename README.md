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
- 可选话题跟踪把日报和范围摘要中的跨天、跨群讨论整理成时间线，支持人工合并、拆分、更名和归档。

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

默认直接调用 DeepSeek V4.1 Flash 的 OpenAI-compatible 接口。该模型在 DeepSeek API 中的模型 ID 为 `deepseek-flash`。JSON 输出模式用于降低摘要响应格式错误的概率。网页保存的 API Key 优先使用；若尚未通过网页保存，则沿用环境变量、`ai.api_key_file` 等原有来源。密钥不会写入日志或项目文件。

```yaml
ai:
  provider_priority: [compatible]
  base_url: https://api.deepseek.com
  model: deepseek-flash
  json_mode: true
  api_key_env: DEEPSEEK_API_KEY
  ui_api_key_file: "D:/Dev/QQDigest/secrets/deepseek-api-key.txt"
```

若密钥保存在项目外的单行文本文件，可另设 `ai.api_key_file` 为该文件的绝对路径；不要把密钥写进配置或提交到 Git。

登录网页后在“AI 设置”输入并保存 DeepSeek Key，再按“测试连接”验证。保存本身不会发起网络请求；Key 写入上方的本机项目外路径，旧 Key 不会在网页回显。报告详情中的“问 DeepSeek”可针对该报告提问和追问：系统只取同群、同日期范围的已归档原始消息；上下文过长时按问题筛选消息及邻近记录。选中的聊天片段会发送到 DeepSeek，答案所列来源 ID 由后端核验。问答只在当前页面内存中保留，关闭对话框或刷新页面即清空；不会写入报告或数据库。

如需使用本机 `codexID` ChatGPT bridge，可在确认包装器和账号池可用后，将 `chatgpt_bridge` 显式加入 `provider_priority`。

新生成的日报和范围摘要会为概览、话题、结论、资源、任务及未解决问题记录原消息引用。在「摘要」打开报告后，可从「逐条来源」查看发送人、时间及邻近上下文；没有有效引用的内容标为「待核实」。旧报告不会自动推断来源，重新生成后才会得到新格式。引用只允许指向该次实际提供给模型的消息，并在打开时再次校验群和报告日期。

知识库 Markdown 文件可使用相对或绝对路径：

```yaml
knowledge:
  resource_path: knowledge/resources.md
  experience_path: knowledge/experiences.md
```

相对路径以项目数据目录为基准。Web 审核和 QQ Bot 指令会写入同一目标文件，并在归档库记录对应路径。

## 话题持续跟踪

在「设置 → 功能开关」开启「话题跟踪」，再从「摘要 → 更多工具 → 话题跟踪」进入。新功能默认关闭；关闭后保留记录和人工纠正，重新开启可继续使用。页面打开或点击「更新话题」时只整理本地已生成的日报与范围摘要，不增加 AI 调用。

相同的具体标题经过大小写、空白及 Unicode 规范化后自动关联，版本号中的有意义分隔符保留；过短或泛化标题保持独立。相似标题只提供人工建议，规则不能识别所有同义表达，也不能仅凭同名判断是否同一业务。列表可按标题关键词、群、日期和状态筛选；筛选用于查找相关话题，详情仍展示整个话题的讨论记录。

详情可更名、归档／重新跟踪、合并话题，把单条讨论关联到其他话题或拆为新话题。人工关联随归档保存，刷新不会覆盖。不同窗口修改同一内容时提示版本冲突，需要重新打开详情后操作。同标题、同来源的重复条目以正文区分，重排不会把人工纠正转给另一条；已消失或变化的重复条目保留为旧版记录。唯一标题且引用不变的措辞更新沿用既有人工关联。

结论及问题须与讨论共享经过群和日期校验的原消息才能自动归属；按钮可查看该讨论、结论及问题的有效引用和连续上下文。旧摘要无引用会明确说明，无法核实的内容不作为最新进展。问题只是最近记录的问题，程序不会自动判定是否解决。文件读取失败会提示数量并保留旧记录；旧版讨论继续保留自身的证据。

本次项目检查和验证范围见 [检查记录](docs/superpowers/specs/2026-10-05-project-audit.md)，后续迭代见 [路线图](docs/ROADMAP.md)。

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
群组页会显示逐群最近成功采集时间、自动同步游标和失败原因。点击“扫描本地群聊”后，可比较本地 QQ 源库和归档库的最近消息，提示可能漏采的群；“补采”会打开已选群和日期的采集页。先用“检查缺口”逐日统计所选范围内未归档的消息，再用“刷新并补采”一键刷新 QQ 数据库并去重补齐。检查不会修改归档或采集状态；补采旧日期不会回退自动同步游标。扫描页仅根据最新消息提示疑似缺口，历史范围的准确缺口以“检查缺口”结果为准。

每日摘要会在计划时间执行；失败时按配置间隔重试，程序启动后也会检查最近 3 天内失败或错过的日报，并补跑遗漏日期。补跑记录保存对应报告日期，不占用当天日报的重试次数；已有有效报告会复用。总览页分别显示消息同步和日报状态，日报失败不会被后续成功的消息同步掩盖。API Key 可在「AI 设置」中保存到程序专用的本机文件；读取时遇到短暂文件占用会重试。

## 启动

### Windows 桌面版

桌面版在独立窗口中显示现有界面，不需要打开浏览器。开发环境可运行：

```powershell
D:\CodexTools\python\Scripts\python.exe -m pip install -e ".[desktop,desktop-build]"
D:\CodexTools\python\Scripts\python.exe -m qq_digest.desktop
```

构建可双击的 EXE：

```powershell
D:\CodexTools\python\Scripts\python.exe scripts/build_desktop.py
```

构建结果为 `D:\Apps\QQDigestDesktop\QQDigestDesktop.exe`，其旁边的 `_internal` 目录是运行所需依赖，应与 EXE 一起保留。`launcher.json` 保存现有 `config/config.yaml` 的路径；归档、报告和知识库仍位于原项目目录。默认构建缓存为 `D:\Cache\QQDigestDesktop`。如果要连接别处的数据，可修改 `launcher.json` 的 `config_path`。

桌面版“设置”可选择新的空文件夹，将消息数据库、报告、知识库及工作文件复制过去，并更新桌面启动配置；旧目录保留，重启后切换。摘要可按 Markdown、JSON 或两种格式导出，默认导出根目录为 `D:\Downloads\QQDigestReports`；每次导出建立独立子目录。开机自启开关写入当前 Windows 用户的启动项，初始为关闭。桌面程序运行期间，连接同一本地服务的浏览器也可以使用设置页。

设置页还可打开当前存储目录和导出目录，并一键生成包含消息数据库、报告、知识库和配置的 ZIP 备份；备份默认放在 `D:\Downloads\QQDigestBackups`。备份会跳过可重建的 `work/snapshots` QQ 临时快照。新备份包含逐文件 SHA-256 清单，并在完成写入后才出现在最近备份列表。自动备份默认每周检查一次，可改为每天或关闭；程序运行时检查，不会自动删除旧备份。文件夹选择窗口若被系统阻止，可在路径框中直接输入完整路径。

恢复时在设置页选择 ZIP，先检查文件清单、ZIP、SQLite 数据库完整性和消息表结构，并预览消息、报告、知识条目数量，再指定一个空目录。程序会先将当前数据另存为安全备份，随后把选中的数据恢复到新目录并更新启动配置；旧数据保留，重启桌面程序后生效。旧版无清单的 ZIP 仍可恢复，但预览会提示无法验证逐文件哈希。若旧备份缺少原本保存在数据目录内的会话密钥，恢复时会在新目录生成密钥，原有网页登录状态随之失效。

“补看”页将各群已生成日报中的讨论、结论、资源、任务及未解决问题放在一个列表里，可按上次查看后、今天或最近七天筛选并逐条标记已读。首次打开时，“上次查看后”默认显示最近七天。新报告的来源按钮会打开原消息及前后文；旧报告或无有效来源的条目会给出提示。补看只读取现有日报，不会重新调用 AI。若某天尚无日报，可在“摘要”页生成。

“待办”页汇集最近 30 天日报中有有效原消息引用的任务建议；先查看原消息，再填写负责人和准确的截止日期，确认后才成为正式待办，也可忽略。旧报告和无有效来源的任务不会自动进入建议。搜索结果中的聊天消息可直接点“加入待办”。正式待办支持编辑、完成、取消、重新打开，始终保留创建时的来源消息。今天到期和逾期数量显示在待办页及总览入口；到期通知可在提醒规则中配置。

“设置 → 界面与功能”可开启默认关闭的“提醒规则”，随后在设置分类中进入提醒页。支持关键词、新资源候选、待办到期和运行故障，按群选择程序内提醒、Windows 通知及免打扰时段。关键词为任一字面匹配；新建、重新启用或更改匹配条件后，历史消息、资源和故障不补发，当前已到期的未完成待办会提醒。仅改名称、时段或渠道不会跳过尚未检查的新增来源。预览只读本地数据、不发送通知；消息和资源预览最多检查最近 200 条来源、展示 20 条匹配示例。

程序运行期间每 60 秒检查，关闭程序或提醒功能后暂停。免打扰期间的新提醒持久排队，结束后再次检查待办和故障是否仍有效，再发布或投递；已有提醒仍可阅读。规则及已读状态随归档备份保存，重复扫描和重启不会重复通知，同群同链接资源按规则去重。Windows 每轮最多提交一条汇总通知；失败最多三次退避，结果未知时停止自动重发，可在投递记录中手动重试。系统接受请求不代表必定显示横幅，Windows 通知设置仍会影响显示。纯浏览器服务保留 Windows 排队，需使用桌面版投递；连接桌面后台的浏览器可查看其通知能力。

### 浏览器入口

Windows 下可双击项目根目录的 `start-web.cmd`。脚本检查服务状态，未启动时后台启动，并打开 `http://127.0.0.1:8765/`。也可手动运行：

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
