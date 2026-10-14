# 全功能审查修复验收：2026-10-14

基线 `ade6235`，修复提交 `377416d`，开发分支 `codex/full-audit-remediation-20261014`。本轮完成 [全功能审查](../../FUNCTIONAL_AUDIT_2026-10-14.md) 的 F01–F17、旧交互审查剩余 12 项，以及旧报告来源 GET 端点的快照兼容问题。历史审查正文保留，以下是修复后的状态。

## 修复与证据对应

| 编号 | 最终行为 | 回归证据 |
| --- | --- | --- |
| F01 | 线程待办操作使用独立数据库连接；并发确认不留下重复任务，并发忽略返回正常冲突 | `test_content_remediation.py` |
| F02 | 不可变生成标识、人工编辑标记保护人工候选；原消息保存使用独立人工身份，不覆盖已有人工编辑 | `test_content_remediation.py`、`test_knowledge_publication_concurrency.py` |
| F03 | 候选编辑检查原 revision，过期提交 409；明确核对最新值后保留草稿再手动提交 | `test_audit_web_contracts.py`、浏览器候选冲突流程 |
| F04 | 群设置只提交变化字段并检查原 revision；核对冲突时合并最新未修改字段 | 同上、`audit_group_ui.cjs` |
| F05 | CLI 增量采集使用完整同步游标；有限手工补采不推进完整游标 | `test_ingestion_remediation.py` |
| F06 | 刷新安装失败恢复全部旧文件；回滚失败保留备份及明确错误 | 同上，真实 Windows 文件锁与故障注入 |
| F07 | 空 Session secret 在打开归档前拒绝桌面启动 | `test_storage_audit_regressions.py` |
| F08 | 自定义受管知识／会话文件迁移保留；外部相对引用保持原绝对身份 | 同上、`test_desktop_settings.py` |
| F09 | 恢复中间状态持久化为 recovering，再次中断后继续恢复 | 同上、`test_desktop_updates.py` |
| F10 | Bot 稍后／忽略仅允许合法状态，拒绝已确认候选 | `test_ingestion_remediation.py` |
| F11 | Bot 与网页统一知识发布；数据库写锁在文件快照之前取得，回滚在锁内执行 | 同上、`test_knowledge_publication_concurrency.py` |
| F12 | legacy protobuf 合并全部消息元素、文字与附件标记 | `test_ingestion_remediation.py` |
| F13 | 备份和迁移拒绝目录／祖先 junction；不复制目录外目标 | `test_storage_audit_regressions.py`，真实 junction |
| F14 | 恢复在 staging 内校验启动密钥与路径，失败不占目标 | 同上、`test_backup_restore.py` |
| F15 | 版本更新过滤空 QQ 源路径，避免误把 EXE 目录当数据 | 同上 |
| F16 | 正式待办草稿按 task_id 保留，原 revision 不被列表刷新重置；冲突可明确核对后重试 | `test_content_ui.cjs`、浏览器正式待办流程 |
| F17 | 群 PATCH 严格枚举／类型／字段校验，不存在群 404；版本超出 SQLite 整数范围 422 | `test_audit_web_contracts.py` |
| 旧 07、08、09 | 群入口可浏览有范围的消息；保留发送人／类型及单边日期条件；无 QQ 号可以更新合法已有库；更新日期沿完整水位 | `test_generated_reading_scope.py`、`test_ingestion_remediation.py`、浏览器 |
| 旧 11、12 | 修改导入日期后阻止 Next／Finish，需重新导入；桌面备份目录按钮可用 | `first_use_dates_ui.cjs`、浏览器 |
| 旧 16、22 | 候选完整分页并返回总量；本次阅读只接受实际生成／复用的 report keys | `test_content_remediation.py`、`test_generated_reading_scope.py` |
| 旧 24、25 | 先读已持久化话题，刷新失败保留内容；提醒精确定位消息／候选／待办，无法核实的旧来源不猜测 | `test_content_remediation.py`、`test_content_ui.cjs`、`reminder_ui.cjs` |
| 旧 27、28、29 | 停用群附件入口禁用并说明；弹层锁滚动、隔离背景、约束 Tab；401 在原页登录保留草稿及版本，不自动重放写入 | 浏览器、`audit_session_ui.cjs`、`audit_summary_auth.cjs`、`summary_progress.cjs` |
| 兼容来源端点 | 旧 GET 来源检查读取选定／当前不可变快照，工作 JSON 改变不破坏合法引用 | `test_generated_reading_scope.py` |

## 最终验证

- Python 全量：**1421 passed、2 skipped、1 warning，退出 0，496.64 秒**。最终源码和 backend id 更新后执行；两项跳过为 Windows 符号链接权限，junction 已实际覆盖。警告为 Starlette TestClient/httpx 弃用提示。
- JavaScript：**36 个 CJS 脚本通过、0 失败**，包括真实 api 与生成／进度模板联动的 401 回归；已有后台任务在登录过期时暂停查询，显式登录恢复后只重连 GET，拒绝的 POST 不重放。
- Chromium：25 个页面／视图在 1280×820、390×844 共 **50 次检查**，无阻断、pageerror 或横向溢出；另外执行群范围、群／候选并发核对、登录取消与恢复、Tab／滚动、精确消息链接、正式待办草稿、向导日期变化等操作。检查了宽窄屏截图。
- 各功能域完成规格与独立质量审查；反馈修正后定向 Python／CJS 重新通过。新回归通过旧代码失败、线程屏障／故障注入或实际浏览器复现确定问题，未仅照抄实现。
- 实际隔离 EXE：`/healthz`、backend id、登录、会话过期保留草稿、显式重试、入库、撤销、群消息浏览均通过，无 pageerror。首轮 readiness 等待超时，独立重启诊断已看到登录窗口，首次超时原因未确定；第二轮测试脚本等待空 `currentCandidate` 报错，补有效对象保护后在全新合成配置完整通过。两者未据此修改产品源码。
- 隔离包及正式包均核对 **110 个打包模块、50 个模板**与当前源码一致，release 清单和文件哈希通过。CLI 不属于桌面打包入口，已由源码全量测试覆盖。
- 主项目 fast-forward 合并后，**113 项相关 Python 回归和全部 36 个 CJS**再次通过；独立读取桌面快捷方式确认 EXE 路径与工作目录正确。入口切换工具退出 0，曾输出 Win32 COM 对象释放提示，随后实际快捷方式与版本登记核对均成立。

命令：

```powershell
& 'D:\CodexTools\python\Scripts\python.exe' -X utf8 -m pytest -o addopts= -q --tb=short
node --test tests/*.cjs
& 'D:\CodexTools\python\Scripts\python.exe' -X utf8 'D:\Cache\QQDigestRemediation-20261014\browser_suite.py'
& 'D:\CodexTools\python\Scripts\python.exe' -X utf8 'D:\Cache\QQDigestRemediation-20261014\package_smoke.py'
```

完整证据在 `D:\Cache\QQDigestRemediation-20261014`：`full-pytest.log/.exitcode`、`node-final.log`、`root/browser-results.json`、`browser-workflow-results.json`、各域 `review` 输出、`package-smoke.json` 和 `package-integrity-*.json`。CJS 和 pytest 内调用的脚本可能重叠，不累计为独立功能数量。

## 本机交付与边界

正式包：`D:\Apps\QQDigestDesktop-2026-10-14-FullAudit-Release`；backend id：`2026-10-14-full-audit-remediation`。沿用原 `D:\Desktop\AI\chatbot\config\config.yaml` 与业务数据，桌面 `D:\Desktop\QQ Digest.lnk` 切换到新包，CandidateWorkflow-Release 保留回退；入口与登记快照保存在证据目录的 `activation-backup`。原配置和三个既有未跟踪文件通过字节哈希核对未变。旧审查文档原稿保存在 `main-audit-original.md`，主项目增加最新修复状态。

源码合入主项目。正式包本轮未用生产配置再次启动，退出旧 QQ Digest 窗口后重新打开桌面快捷方式生效。隔离包为 `D:\Apps\QQDigestDesktop-2026-10-14-FullAudit`，合成配置和数据在证据缓存；公开安装包未更新。未关闭、更新或改动 Codex 应用。

本轮未调用真实 QQ 解密／采集、DeepSeek／ChatGPT 桥接、识图、QQ Bot 平台投递、真实通知或开机自启，没有据模拟测试声称这些外部链路全部实机通过。多文件刷新保证可捕获异常的回滚，不宣称突然断电时跨文件原子性；模型初始化失败后的日报采集、源 rowid 时间顺序等原审查假设仍没有新增实机结论。P2-07 按群云 AI 权限继续为计划项，本轮未新增该功能。
