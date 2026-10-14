# 全功能审查记录：2026-10-14

**修复状态更新：2026-10-14。** F01–F17、旧审查剩余 12 项及兼容来源端点均已处理，代码与桌面入口已更新。最终 1421 项 Python 回归、36 个 CJS、50 次页面检查、关键操作及隔离 EXE 验证通过；外部链路边界保留。见 [修复验收](superpowers/specs/2026-10-14-full-audit-remediation-verification.md)。以下正文保留最初审查的基线、问题表现及当时测试结果，表中的“当前状态”指审查当时，不代表修复后的状态。

基线：`codex/public-beta-2026-10-06`，提交 `ade6235`。本轮检查已有功能，未实施修复、部署或修改真实业务数据。以下优先级是修复顺序，不是安全漏洞评级。

结论：**确认 17 项新增缺陷（9 项 P1、8 项 P2）**，并复核了上次审查仍未完成的 12 项。现有测试全部通过，不能覆盖本轮发现的跨请求事务、人工编辑保护、文件发布中途失败等分支。

## 实际执行与覆盖边界

| 检查 | 当前结果 | 证据 |
| --- | --- | --- |
| 全量 Python 回归 | 1315 passed、2 skipped、1 warning；退出 0，442.81 秒 | [完整日志](D:/Cache/QQDigestFullAudit-20261014/full-pytest.log)、[退出码](D:/Cache/QQDigestFullAudit-20261014/full-pytest.exitcode) |
| 现有 CJS 检查 | 31 个脚本通过，0 失败；与 pytest 内调用的脚本可能重叠，不累计为独立功能数量 | [CJS 日志](D:/Cache/QQDigestFullAudit-20261014/cjs-tests.log) |
| 浏览器检查 | 25 个页面／视图状态，1280×820 与 390×844 共 50 次；HTTP 正常，无 pageerror、空白页面或整页横向溢出 | [浏览器结果](D:/Cache/QQDigestFullAudit-20261014/root/browser-results.json) |
| 路由清单 | 实际应用注册 168 条路由、172 个 HTTP 方法项，含框架文档路由；不是独立功能数或逐条请求通过数 | [路由清单](D:/Cache/QQDigestFullAudit-20261014/root/route-inventory.json) |
| 隔离复现 | 真实 API／业务函数、模板 VM、确定性线程屏障、故障注入、真实 Windows 文件锁／junction | [采集域记录](D:/Cache/QQDigestFullAudit-20261014/ingestion/audit.md)、[内容域记录](D:/Cache/QQDigestFullAudit-20261014/content/content_audit.md) |

测试命令：

```powershell
& 'D:\CodexTools\python\Scripts\python.exe' -X utf8 -m pytest -o addopts= -q --tb=short
node --test tests/*.cjs
& 'D:\CodexTools\python\Scripts\python.exe' -X utf8 'D:\Cache\QQDigestFullAudit-20261014\root_audit.py'
```

两项跳过均为当前环境缺少创建符号链接的权限：`test_mutable_link_rejected`、`test_symlink_ancestor_and_child_are_skipped`，另行定点重查仍跳过。真实 Windows junction 的隔离复现可以执行。warning 为 Starlette TestClient 使用 httpx 的弃用提示，不是业务失败。

浏览器包含首页、运行记录、群聊、采集、报告阅读／分群／管理、搜索、归档问答、候选、知识库、收藏、待办、话题、提醒、首次设置、版本更新、设置的 AI／自动运行／功能／存储／备份分区和两个旧入口重定向。实际点击确认了关键词查找、群详情、候选编辑保存→入库→撤销、报告详情；候选编辑保存后仍 pending，入库 confirmed，撤销回 pending，当前已修复流程成立。

浏览器使用合成归档、合成报告和桌面设置替身，禁用应用 lifespan，未启动自动采集、自动摘要或通知。`/api/storage` 与 `/api/desktop-updates` 的浏览器响应被设为说明性的 503，以检查不可用反馈；这些接口的业务由源码、现有测试和独立复现覆盖。页面正常打开不代表每个按钮、数据规模或外部链路都完成实机验收。

## 功能覆盖清单

| 功能域 | 已检查内容 | 主要问题／限制 |
| --- | --- | --- |
| QQ 数据源与采集 | NTQQ、FTS／legacy protobuf、刷新、身份解析、去重、增量水位、手工补采、CLI、历史巡检、批量群操作 | F05、F06、F12；未操作真实 QQ 进程或数据库 |
| 摘要生成与发布 | 日报／范围摘要、精确时间窗、分群失败、输入指纹、文件与数据库发布、后台进度／恢复、重试／完整性 | F02；模型由本地 FakeAI 替代 |
| 阅读与报告工具 | 要点／分群／管理、筛选／分页／已读、来源上下文、报告问答、版本／纠错／重生成 | 旧 #22；另列兼容来源端点不一致 |
| 搜索与归档问答 | 中文检索、消息浏览、身份显示、群／日期／类型范围、分页、预览和证据边界 | 旧 #07；没有实测模型回答质量 |
| 群设置 | 添加／删除、关注、每日摘要、分类、关键词、首次窗口、附件开关、群批量设置 | F04、F17；旧 #08/#09/#27 |
| 候选与知识库 | 编辑、审核／稍后／恢复／撤销、来源、知识文件与记录、人工保存／重生成 | F02、F03、F10、F11；旧 #16 |
| 待办 | 建议提取／确认／忽略、消息创建、编辑／状态／冲突、来源和草稿 | F01、F16 |
| 话题、提醒与收藏 | 持久化、刷新、详情／来源、规则、未读／分页、收藏／稍后阅读 | 旧 #24/#25；原生通知投递未执行 |
| AI、附件与用量 | bridge／兼容接口选择、限额／计价／重试、密钥保存、附件范围／缓存／读取开关、取消等待 | 外部连接、真实费用和附件兼容未实测 |
| 首次设置与登录 | 向导数据源／群／导入／预览，密码与会话验证，权限和功能开关 | F07；旧 #11/#29 |
| 存储、备份与恢复 | 清单／策略／清理、导出、路径约束、迁移、ZIP 校验、恢复、安全备份、切换后只读 | F08、F13、F14；旧 #12 |
| 桌面与版本管理 | manifest、版本登记／校验、事务状态、回退、路径／入口、构建清单、公共发行隔离 | F09、F15；未启动正式 EXE 或操作快捷方式／注册表 |
| QQ Bot、通知与后台 | 白名单、命令、持久发送／重试、调度、线程取消、操作协调、存储切换 admission | F10/F11；未实际发信 |

各分域记录包含检查过的源码／模板／测试清单。审查覆盖所有已有功能域，**不声称 111 个 Python 文件全部逐行审读，也不声称全部外部功能实机通过**。

## 17 项新增缺陷

### F01 · P1：并发确认待办共用事务，失败请求仍留下任务

位置：[task_routes.py:50](D:/Desktop/AI/chatbot/qq_digest/web/task_routes.py:50)、[task_inbox.py:109](D:/Desktop/AI/chatbot/qq_digest/task_inbox.py:109)、[archive.py:56](D:/Desktop/AI/chatbot/qq_digest/archive.py:56)。两个 worker 使用同一个 SQLite 连接，commit／rollback 会影响另一请求。

复现：让第一次真实确认在插入任务后暂停，第二次确认同一建议成功提交，再恢复第一次。第一请求 400、第二请求 200，但最终 **tasks=2、decision=1**。未 mock 业务或注入业务错误，只控制线程调度。最小处理是为线程中的待办操作使用独立连接，参照已有话题／提醒 worker。

证据：[脚本](D:/Cache/QQDigestFullAudit-20261014/content/proof_task_shared_transaction.py)、[结果](D:/Cache/QQDigestFullAudit-20261014/content/proof_task_shared_transaction_results.json)。

### F02 · P1：重生成报告覆盖或删除人工保存的候选

位置：[candidates.py:41](D:/Desktop/AI/chatbot/qq_digest/candidates.py:41)、[archive.py:558](D:/Desktop/AI/chatbot/qq_digest/archive.py:558)、[report_regeneration.py:96](D:/Desktop/AI/chatbot/qq_digest/report_regeneration.py:96)。pending 候选被当作可刷新、可清理的 AI 草稿，没有区分人工编辑。

真实重生成函数＋FakeAI：保持标题、保存人工正文→重生成，正文／理由变回 AI 文本；修改标题后再重生成，旧候选 GET **404**，仅剩新 AI 候选。人工编辑不在不可变报告历史中。需要保留人工修改标识，并阻止该记录被 AI 刷新或无引用清理。

### F03 · P1：候选旧窗口静默覆盖另一窗口已保存的内容

位置：[app.py:1595](D:/Desktop/AI/chatbot/qq_digest/web/app.py:1595)、[candidates.py:176](D:/Desktop/AI/chatbot/qq_digest/candidates.py:176)。编辑提交完整字段，无版本条件。

A 读取旧草稿→B 保存新标题／正文（200）→A 修改正文并提交旧标题（200），B 的标题消失。需要在读写契约中使用 revision 和原子条件，冲突返回 409 并保留草稿。F02/F03 证据：[脚本](D:/Cache/QQDigestFullAudit-20261014/content/proof_content.py)、[结果](D:/Cache/QQDigestFullAudit-20261014/content/proof_content_results.json)。

### F04 · P1：群设置旧窗口覆盖附件读取开关

位置：[group_details.html:48](D:/Desktop/AI/chatbot/qq_digest/web/templates/group_details.html:48)、[app.py:983](D:/Desktop/AI/chatbot/qq_digest/web/app.py:983)。详情保存提交全部设置，没有版本校验。

B 把分类设为 study 并开启图片读取→A 只编辑关键词、提交旧表单，两次均 200；最终分类 general，图片读取 false。同样可覆盖另一窗口刚关闭的开关。旧 #01 修复了字段缺失，但未覆盖多窗口过期表单。最小处理：表单提交实际变更字段，涉及同字段冲突时按版本拒绝。

证据：[群接口复现结果](D:/Cache/QQDigestFullAudit-20261014/root/group-proof-results.json)，`stale_group_save`。

### F05 · P1：CLI sync 采当天却推进完整历史水位，跳过离线消息

位置：[cli.py:273](D:/Desktop/AI/chatbot/qq_digest/cli.py:273)、[cli.py:281](D:/Desktop/AI/chatbot/qq_digest/cli.py:281)。

合成源上次同步七天前，三天前及今天各有消息。CLI 只读今日 00:00～12:00，游标推进到 12:00；随后的周期同步只读 11:00～12:00，最终只有今日消息。旧消息未物理删除，但正常增量路径跳过了它。应复用已有增量同步；明确范围补采不推进完整水位，同类 backfill 也需检查。

证据：[cli_cursor.py](D:/Cache/QQDigestFullAudit-20261014/ingestion/cli_cursor.py)、[详细实际输出](D:/Cache/QQDigestFullAudit-20261014/ingestion/audit.md)。

### F06 · P1：源数据库刷新只替换一半，失败不恢复原完整集合

位置：[refresh.py:202](D:/Desktop/AI/chatbot/qq_digest/refresh.py:202)。核心文件先校验齐全，再依次 replace；后续失败不回滚已替换项。

真实 Windows 文件锁占住第二个合成目标：刷新报 PermissionError，最终 `group_info.db=NEW`、`nt_msg.db=OLD`。应在整体成功前保留旧目标，安装失败恢复整个集合。现有测试只覆盖安装前文件不齐全。

证据：[真实锁复现](D:/Cache/QQDigestFullAudit-20261014/ingestion/real_refresh_lock.py)、[分域记录](D:/Cache/QQDigestFullAudit-20261014/ingestion/audit.md)。

### F07 · P1：桌面服务接受空会话密钥，认证可被绕过

位置：[desktop.py:130](D:/Desktop/AI/chatbot/qq_digest/desktop.py:130)、[config.py:174](D:/Desktop/AI/chatbot/qq_digest/config.py:174)。CLI 拒绝空密钥，桌面入口未拒绝。

仅在合成配置中移除环境变量和 session 文件，真实 make_server 创建空密钥签名器；未登录请求 401，按该空密钥构造的 Cookie 请求 200。没有读取真实凭据，**没有证明当前正式配置使用空密钥**。最小处理：创建认证服务前统一拒绝空密钥。

### F08 · P1：存储迁移漏复制自定义相对路径，却切换成功

位置：[desktop_settings.py:63](D:/Desktop/AI/chatbot/qq_digest/desktop_settings.py:63)。配置路径不是 absolute 时直接跳过，固定目录复制也不包含自定义 notes。

合成配置 `knowledge.resource_path: notes/resources.md`：原文件存在，目标文件不存在，launcher 已切换。自定义相对 session 文件受相同分支影响。原数据仍在旧目录，但切换后无法使用。应按源 data_dir 解析所有配置路径，完整复制受管文件后再切换。

### F09 · P1：版本恢复再次中断，持久状态丢失恢复意图

位置：[desktop_updates.py:121](D:/Desktop/AI/chatbot/qq_digest/desktop_updates.py:121)、[desktop_updates.py:138](D:/Desktop/AI/chatbot/qq_digest/desktop_updates.py:138)。starting／committing／recovering 被判为 interrupted 后，马上保存成普通 waiting。

在该保存点终止的故障注入：下次不调用 stop_existing，不执行确定性回退，重走目标启动分支。真实正常同端口若仍被占用会拒绝启动；**不能据此断言实际同端口必然双写**。缺陷是恢复意图不持久，可能取消而不完成回退，旧 URL 空闲时还可能再次启动目标。应保留恢复状态并先处理已有 child。

F07～F09 证据：[storage/reproduce.py](D:/Cache/QQDigestFullAudit-20261014/storage/reproduce.py)。脚本使用合成数据／Runtime，首次已成功执行；重跑需要新建隔离子目录。

### F10 · P2：QQ Bot 可直接改变已入库状态，知识文件与数据库分裂

位置：[commands.py:167](D:/Desktop/AI/chatbot/qq_digest/notify/commands.py:167)、[commands.py:178](D:/Desktop/AI/chatbot/qq_digest/notify/commands.py:178)。Bot 的 later／ignore 未沿用 Web 的状态前置条件和撤销入库流程。

已入库→Bot later：知识列表 1→0，Markdown 仍在；编辑该候选后再次入库，库内展示新标题，文件仍保留旧标题。应通过统一知识服务改变状态，confirmed 回审核必须显式撤销。

### F11 · P2：QQ Bot 入库失败，仍留下未确认的知识内容

位置：[commands.py:149](D:/Desktop/AI/chatbot/qq_digest/notify/commands.py:149)。write→record→confirm 没有统一回滚。

在元数据记录处注入 OSError：返回失败、候选 pending、知识记录 0，但 Markdown 已含该条内容。后续知识去重仍可能使用这份未确认材料。最小处理：Bot 调用现有 KnowledgeLibrary.confirm_candidate。F10/F11 证据：[ingestion/proof.py](D:/Cache/QQDigestFullAudit-20261014/ingestion/proof.py)、[分域实际输出](D:/Cache/QQDigestFullAudit-20261014/ingestion/audit.md)。

### F12 · P2：legacy protobuf 只读首元素，丢失图片配文／后续文字

位置：[protobuf.py:114](D:/Desktop/AI/chatbot/qq_digest/collector/protobuf.py:114)。重复 40800 字段只取首项。

合法合成的图片＋文字返回 `[图片]`，后续文字丢失；多段文字同理。影响限定为 legacy protobuf／FTS 缺失的补充路径，未验证真实 QQ 此类消息频率。应遍历全部元素并合并文本。证据同 [proof.py](D:/Cache/QQDigestFullAudit-20261014/ingestion/proof.py)。

### F13 · P2：备份和迁移跟随 junction，纳入受管目录外的内容

位置：[backup_restore.py:131](D:/Desktop/AI/chatbot/qq_digest/backup_restore.py:131)、[desktop_settings.py:78](D:/Desktop/AI/chatbot/qq_digest/desktop_settings.py:78)。文件级 is_symlink 未检查祖先 junction，copytree 也会跟随。

真实 Windows junction 的目标仅为审查缓存内的“外部”合成目录；备份和迁移均包含该目标文件。可能造成非预期内容复制或体积膨胀。应沿用已有 safe_path，拒绝源树的目录及祖先重解析点。测试 junction 已移除，目标内容保留。

### F14 · P2：恢复在发布目标后才校验 session，失败占住重试目录

位置：[backup_restore.py:362](D:/Desktop/AI/chatbot/qq_digest/backup_restore.py:362)、[backup_restore.py:372](D:/Desktop/AI/chatbot/qq_digest/backup_restore.py:372)。

合成备份含过短密钥：预览 valid=true，恢复报 ConfigError，目标已是非空目录，原 launcher 未变。同路径重试被拒绝；没有删除原数据。应在 staging 内完成启动所需校验后发布，或回滚发布后的失败。

### F15 · P2：空 QQ 源路径被当作 EXE 目录，阻止版本切换

位置：[desktop_settings.py:279](D:/Desktop/AI/chatbot/qq_digest/desktop_settings.py:279)、[desktop_updates.py:77](D:/Desktop/AI/chatbot/qq_digest/desktop_updates.py:77)。data_paths 无条件包含默认空 db_dir，Path('').resolve() 变成工作目录。

使用实际 _updates，合成 release/state，把工作目录设为合成 EXE 目录；prepare 误判程序与数据目录重叠而拒绝。应过滤空配置路径。F13～F15 证据：[storage/reproduce_paths.py](D:/Cache/QQDigestFullAudit-20261014/storage/reproduce_paths.py)。

### F16 · P2：编辑正式待办时，另一卡片状态操作清空草稿

位置：[tasks.html:36](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:36)、[tasks.html:37](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:37)。只保存建议草稿，正式任务的表单每次全量重建。

实际模板 VM：编辑任务 2 写入未保存标题→完成任务 1→列表刷新，任务 2 标题回旧值且编辑表单隐藏。这是旧 #17 的另一分支，已修的是建议卡片。可只刷新改变卡片，或按 task_id 保留草稿和原 revision。

证据：[模板复现](D:/Cache/QQDigestFullAudit-20261014/content/proof_content_ui.cjs)、[结果](D:/Cache/QQDigestFullAudit-20261014/content/proof_content_ui_results.json)。

### F17 · P2：群 PATCH 校验不足，非法分类能破坏后续群读取

位置：[app.py:983](D:/Desktop/AI/chatbot/qq_digest/web/app.py:983)、[models.py:32](D:/Desktop/AI/chatbot/qq_digest/models.py:32)。分类任意值直接入库，之后反序列化为 Literal 失败。

真实 API：提交 unsupported-value 返回 200，随后 `/api/groups` 返回 **500**。另有 `enabled:'false'` 被当成开启、更新不存在群仍返回 200。分类选择 UI 不会产生此值，触发范围为异常／自定义接口调用。应统一严格验证字段及枚举，检查命中行数，错误请求不能改变有效数据。

证据：[root_audit.py](D:/Cache/QQDigestFullAudit-20261014/root_audit.py)、[结果](D:/Cache/QQDigestFullAudit-20261014/root/group-proof-results.json)。

## 上次剩余 12 项的复核

不混入上述 17 项新增数量。历史编号来自 [10 月 9 日审查](D:/Desktop/AI/chatbot/docs/INTERACTION_REVIEW_2026-10-09.md)。

| 旧编号 | 当前状态 | 本轮证据类型 |
| --- | --- | --- |
| 07 | 群“查看消息”仍进入空关键词首页 | 浏览器：群已有消息，页面要求从关键词开始 |
| 08 | 已有解密库允许无 QQ 号导入，但常规更新仍固定刷新 | 首次设置与采集源码调用链 |
| 09 | 已有任意消息时更新默认近七天，不按离线水位补齐 | 浏览器 update_url 为 10-08～10-14；源码 |
| 11 | 导入完成后改日期，继续条件不作废原完成状态 | 向导源码链 |
| 12 | 备份页“打开备份目录”初始化后仍禁用 | 浏览器及初始化源码 |
| 16 | 候选超过 200 条截断，无分页／总量／说明 | 查询与数量显示源码链 |
| 22 | 阅读“本次生成”按群／日期读所有报告，可能混入旧报告／其他类型 | 阅读切换源码链 |
| 24 | **部分修复**：读取路由锁已移除；首次刷新失败仍不加载已有话题 | 模板 VM 无后续 GET，当前路由源码 |
| 25 | 提醒来源链接仍不能定位具体消息／候选／待办 | 提醒链接生成与目标页源码 |
| 27 | 停用群附件入口仍可点击，服务拒绝的交互文案不一致 | 附件 UI 与服务链 |
| 28 | 弹层缺少完整的焦点／背景隔离 | 源码；候选弹层 body overflow 仍 visible，未逐个实测键盘环 |
| 29 | 登录过期跳转丢失未保存草稿与原返回地址 | base api 401 与登录重定向源码 |

已修复的 #01～06、#10、#13～15、#17 建议草稿、#18～21、#23、#26 在当前修复链路／回归中成立，没有依据旧文档重新报为同一缺陷。F04 和 F16 是已修问题未覆盖的另一操作分支。

## 较低优先级的不一致与未验证假设

当前报告详情读取不可变快照，但旧 GET `/sources/{msg_id}` 仍用可变 JSON 校验。在合法快照引用 m1、工作 JSON 损坏／改变的合成情况中，详情有来源，旧 GET 返回 404；当前 UI 的 POST related-messages 同时返回 200。属于兼容端点不一致，未计入 17 项主缺陷。证据：content 的 `snapshot_source_file_mismatch`。

AI 初始化异常会在日报采集前结束，合成验证 collector build／refresh 均为 0；是否要求该日报流程仍采集属于需求边界，未当作已确定缺陷。NTQQ 扫描假设 rowid 与消息时间同序，真实历史补写是否违反它未验证。路线图尚未实现的云 AI 分群授权属于未完成功能，未计为当前回归缺陷。

未实测真实 DeepSeek／ChatGPT 本地桥接、QQ 解密提取／原始 schema／WAL、QQ Bot 平台投递、原生文件选择器、通知、开机启动、真实版本切换进程及断电恢复、大附件质量与长时间多进程竞争。因此不能声称“所有功能实机都通过”。

## 建议处理顺序与文件状态

先处理 F01/F02/F06，保护已保存内容和文件集合；紧接 F05/F07/F08/F09，防止漏采、认证失效和切换失败；再处理两个多窗口覆盖问题 F03/F04，最后补齐 P2 及既有 12 项。修复应复用已有连接隔离、知识服务、版本冲突和文件回滚机制，避免新增无必要的统一框架。

本轮仅新增本审查文档。审查开始已有的 `.qqdigest-activity.lock`、未跟踪的 `ai_settings.html`、`catchup.html` 均保留。没有改正式配置、生产数据库、桌面入口、系统设置或 Codex 应用。所有生成脚本、截图、合成数据和测试日志位于 **`D:\Cache\QQDigestFullAudit-20261014`**。
