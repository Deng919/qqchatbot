# QQ Digest 全面检查：2026-10-17

审查基线：`25f7df4`。本轮检查已有功能，未扩展路线图或恢复已取消的隐私控制功能。

## 结论

发现 **7 组可复现的 P2 缺陷**，其中密钥权限问题涉及备份、迁移两条实现路径。它们尚未修复，现有测试通过不覆盖这些新故障场景。没有在本轮范围内确认 P0／P1。

另确认一个会随日期失效的测试断言并已修正；它不属于消息日期过滤功能的产品缺陷。源码与正式桌面包保持同一功能实现，没有部署新功能代码。

## 已确认缺陷

### F01：撤销并编辑知识后，重新保存可能发布错误内容并留下回滚残留〔P2〕

位置：`qq_digest/knowledge_library.py:112-120`，关联 `qq_digest/candidates.py:39-61`。

触发：从原消息保存资源 A；撤销入库；把候选编辑成经验 B，并修改标题、链接和正文；再从原消息按原设置保存资源 A。

事实：不可变的 `generation_key` 命中已编辑的候选，保存函数只回填正文／理由／摘录，随后实际发布经验 B 的标题与链接，接口仍返回 `reused:false`。请求与落库分类、标题、链接不一致，并覆盖候选此前编辑的正文。

同一根因还使 `remember()` 备份请求的资源文件，但 `_publish()` 写入经验文件。注入“写入后抛出 OSError”，数据库回滚为 pending、knowledge_items 为 0，而 experiences.md 仍留下条目。

处理方向：先明确匹配的候选身份，保留用户编辑；新请求不能误复用身份已变化的候选。对最终实际写入的文件建立回滚快照。

证据：`content-review/repro_knowledge_resave.py`、`repro_knowledge_rollback.py`；主审查者独立重现结果在 `root-reproduction.log`。

### F02：报告已提交、通知入队失败后，重跑会漏补通知〔P2〕

位置：`qq_digest/pipeline.py:231-237`、`:304-311`。

触发：日报文件与数据库提交成功后，`enqueue_notification()` 暂时失败；按原日期正常重跑。

事实：第一次状态为 partial_success，报告存在但没有 send_log。重试命中报告指纹直接跳过通知，最终标记 success；通知队列无记录，因此队列重试也无法补发。合成实验重跑后队列数 0、发送数 0、AI 调用数 1。

处理方向：将通知意图与发布一起可靠提交，或在复用报告时幂等补齐缺少的通知；避免重复发送已成功记录。

证据：`ingestion-review/repro_notification_retry.py`、`root-reproduction.log`。

### F03：备份和存储迁移会丢失密钥的 Windows 私有权限〔P2〕

位置：`qq_digest/desktop_settings.py:75`；`qq_digest/backup_restore.py:267-279`。

触发：原 API Key 使用程序设置的私有 DACL；备份或迁移目标位于其他本机用户可读的目录。

事实：`shutil.copy2()` 没有复制 Windows 私有 DACL，迁移密钥继承目标权限；包含密钥和 Session secret 的明文 ZIP 同样继承目标目录权限。独立合成实验原密钥没有广泛读取授权，ZIP 和迁移密钥均新增 Everyone 读取授权。此前代理实验也观察到 Users／Authenticated Users，具体取决于父目录 ACL。

这个结论有明确条件：目标目录本来就较宽松。没有使用其他用户登录读取真实文件，也不声称私有目标目录必然泄露。恢复后的密钥在本次 Windows 实验中仍为私有，不能把问题扩大成“恢复一定丢失权限”。

处理方向：在写入敏感副本（含 `.partial`）前建立私有 ACL，在迁移提交启动配置前核验秘密文件权限；无需引入新的云端隐私策略。

证据：`storage-review/acl-repro/evidence.json`；主审查独立实验 `root-validation/acl-confirmation.json`。

### F04：原数据目录后来变为 junction，会阻止正常备份的预览与恢复〔P2〕

位置：`qq_digest/backup_restore.py:322`，经 `qq_digest/storage_files.py:234` 的历史 owner 归一化。

触发：正常生成备份；移动原数据目录并在旧位置建立 junction；预览同一份未修改 ZIP。

事实：首次预览成功，旧位置变为 junction 后报“存储路径包含链接或重解析点”，恢复也无法通过最初检查。清单内旧根目录仅用于历史路径回填，却错误参与当前实际文件系统安全检查。

处理方向：历史 owner 保持词法身份；继续严格检查真正读取的 ZIP、缓存和恢复目标，避免把历史路径当作正在访问的路径。

证据：`storage-review/historical_root_repro.py`；主审查在独立合成目录重现。

### F05：旧忽略接口可以直接隐藏已入库知识，造成状态分裂〔P2〕

位置：`qq_digest/web/app.py:1804`。

触发：知识已入库，review 开关启用，旧客户端 POST `/candidates/{id}/ignore`。

事实：返回 303，候选变 ignored；知识列表从 1 降到 0，详情 404，但 `knowledge_items` 与 Markdown 条目仍存在；正常撤销接口返回 409。该接口还绕过相邻写操作使用的操作协调。当前界面没有忽略按钮，因此影响范围为保留的旧接口调用者，而非声称点击现有按钮必现。

处理方向：只允许待审核／稍后候选进入忽略，使用共享操作锁与原子状态判断；已入库内容应走撤销流程。

证据：`content-review/repro_confirmed_ignore.py`、`root-reproduction.log`。

### F06：QQ 源路径包含 `#` 时，SQLite URI 截断并可能创建错误空文件〔P2〕

位置：`qq_digest/collector/ntqq.py:360`、`:374`、`:380`。

触发：有效源库位于 Windows 合法目录 `source#hash`。

事实：原始路径直接拼接 `file:<path>?mode=ro`，`#` 被当作 URI 片段，实际打开 `.../source`，并丢失只读参数。合成的有效 NTQQ 数据库被报为“no such table”，错误前缀位置出现空 SQLite 文件。

处理方向：使用已编码的绝对文件 URI，统一这三处只读连接方式。

证据：`ingestion-review/repro.py`；主审查独立确认实际打开路径，见 `root-reproduction.log`。

### F07：设置允许的长模型名无法通过用量记录〔P2，边界输入〕

位置：`qq_digest/config.py:56`、`qq_digest/web/ai_connection_routes.py:29` 与 `qq_digest/ai_usage.py:61,145`。

触发：保存 101–200 字符的网关／自定义模型标识。

事实：连接配置允许最多 200 字符，用量预留和单价配置仅允许 100；108 字符配置验证成功，却在 HTTP 发送前报“无法检查 AI 用量，请检查存储后重试”。模拟传输的实际请求数为 0，修复存储无法解决此错误。

处理方向：统一输入、用量预留与单价记录的长度约束，增加边界回归；让配置可保存与可使用的范围一致。

证据：`ingestion-review/repro.py`、`root-reproduction.log`。

## 未确认的来源假设

`collector/ntqq.py` 的若干倒序 rowid 扫描遇到第一条旧时间消息即停止。合成“较早消息后插入”的数据库能导致最近消息漏读；但本轮未读取真实 QQ 数据库，尚未证实 QQ 是否允许这一行号／时间逆序。因此登记为 R01 来源格式假设，**不计入上述已确认产品缺陷**。

## 测试与运行验证

- 初次全量：1499 passed、1 failed、2 skipped，526.67 秒。唯一失败为 `test_message_browse_keeps_explicit_multiple_group_selection`：消息时间取运行日，查询截止日却固定为 2026-10-15。现已按所配置时区从合成消息时间推导截止日，保留原断言含义；日期与浏览专项 25 项通过。
- 修正测试后的最终全量：**1500 passed、2 skipped、1 warning，518.89 秒，退出码 0**，见 `final-pytest.log`、`final-results.xml` 与 `final-pytest.exitcode`。两项跳过是 Windows 符号链接权限条件；warning 是既有 TestClient／httpx 弃用提示。原始失败日志保留，不能与最终结果混用。
- 38 个 CJS 交互脚本全部通过。
- 未登录 GET 返回码矩阵覆盖 80 个入口，包含预期公开的登录、健康及文档地址；未发现业务入口意外返回 200。参数校验返回 422 的路径没有因此被当作已独立证明 handler 鉴权正确。
- 源码 Chromium 1280／390 两种视口覆盖 25 个页面／分类／别名，共 50 次；隔离实际 EXE 再做 50 次，无阻断性错误、页面脚本错误或横向溢出。
- 两种运行方式都执行了群详情、原消息浏览、关键词搜索、候选编辑／入库／撤销和报告详情。结果均符合预期；更窄的异常链路缺陷见 F01–F07，不能被这些普通路径通过覆盖。
- 当前正式包 `D:\Apps\QQDigestDesktop-2026-10-16-VersionList-Release` 与源码的 111 个模块、51 份模板一致，清单完整性通过。

## 范围、交付和限制

源码审查覆盖采集／刷新／同步／巡检、摘要发布及后台重试、AI／用量／连接路由、报告与来源及问答、知识与候选、待办／收藏／话题／提醒、配置与鉴权、存储与备份、桌面更新／回退／打包。三个独立审查记录与主审查的再现结果均保留。

所有运行验证使用合成数据。源码及 EXE 浏览器审查将桌面版本与存储列表 API 设置为明确的 503 不可用场景，避免对正式注册表、包目录和用户存储执行操作；这些 UI 扫描不等同于实际迁移或跨进程更新验收。备份／迁移另用隔离合成数据测试。

未调用真实 QQ、桥接鉴权、外部 AI、附件服务或通知；未验证具体厂商的在线可用性。隔离 EXE 的初次脚本缺少合成 Session 文件，第二次脚本未把合成归档放到加载器的标准路径；这两项是测试装配错误，修正后完成全部 EXE 检查，不计产品缺陷。

本轮仅修正测试并记录审查结果，F01–F07 未修复；不更换生产桌面入口、不发布新安装包。既有三个未跟踪文件保留。

证据目录：`D:\Cache\QQDigestFullAudit-20261017`。其中 `root/` 为源码截图与结果，`exe-final/` 为实际 EXE 截图与结果，`exe/isolated-package/` 是只指向合成配置的测试副本；日志与重现样本均留在该缓存目录。
