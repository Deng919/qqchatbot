# Audit remediation implementation plan

> 使用 subagent-driven-development 串行实施三个文件组，每组先规格复核再质量复核；复现清单见配套设计和 PROJECT_AUDIT_2026-10-17.md。

**Goal:** 修复 F01–F07，保留现有行为及安全约束。

**Architecture:** 复用发布事务、通知去重、只读 SQLite URI 和私有文件权限；不新增状态服务。

**Tech Stack:** Python、SQLite、Windows ACL、FastAPI、现有模板和 PyInstaller。

## 任务一：存储 F03／F04

文件：`backup_restore.py`、`desktop_settings.py`、必要的 `storage_files.py`／`ai/key_store.py`，以及新的 `tests/test_audit_storage_fixes.py`。

- [ ] 先写共享父目录下密钥／ZIP ACL、权限失败不发布、历史根 junction 后备份预览／恢复回归；运行并记录预期失败。
- [ ] 用最小改动保证写前私有权限，历史身份与实际路径检查区分，保留原安全防护。
- [ ] 执行新增与备份／迁移／存储相关回归；先规格复核，再质量复核并处理问题。

## 任务二：知识 F01／F05

文件：`knowledge_library.py`、必要的 `candidates.py`、`web/app.py`；新增 `tests/test_audit_content_fixes.py`。

- [ ] 先复现人工编辑后重存、写后失败回滚、旧 ignore 对已入库／不存在／普通待审／忙态的行为。
- [ ] 保留编辑候选、新请求身份及正确文件回滚；旧接口在事务和操作锁中进行状态约束。
- [ ] 执行知识／候选／Web 专项；规格、质量复核并处理问题。

## 任务三：通知／采集／模型 F02／F06／F07

文件：`pipeline.py`、`collector/ntqq.py`、`ai_usage.py`，必要的配置与单价输入限制；新增 `tests/test_audit_ingestion_fixes.py`。

- [ ] 先写通知入队失败重试／部分收件人／成功去重／禁用、编码源路径、模型长度边界回归，运行观察失败。
- [ ] 在报告复用中幂等补通知并保留失败、统一 URI 和 200 字符限制。
- [ ] 执行采集／通知／AI／用量／多 AI 专项，规格与质量复核。

## 任务四：整体验证和交付

- [ ] 更新后台标识，运行全部 pytest 与 CJS，执行宽窄屏源码及隔离 EXE 验证。
- [ ] 正式包源码／模板／清单核对，记录合成与真实验证边界。
- [ ] 更新审查状态、路线图和验收，合入主项目，更新桌面入口并核对原配置／既有文件保持不变。

Python 一律 `D:\CodexTools\python\Scripts\python.exe -X utf8`；所有验证日志写入证据目录。不得改真实数据或停止 Codex。
