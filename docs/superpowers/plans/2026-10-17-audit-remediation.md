# Audit remediation implementation plan

> 使用 subagent-driven-development 串行实施三个文件组，每组先规格复核再质量复核；复现清单见配套设计和 PROJECT_AUDIT_2026-10-17.md。

**Goal:** 修复 F01–F07，保留现有行为及安全约束。

**Architecture:** 复用发布事务、通知去重、只读 SQLite URI 和私有文件权限；不新增状态服务。

**Tech Stack:** Python、SQLite、Windows ACL、FastAPI、现有模板和 PyInstaller。

## 任务一：存储 F03／F04

文件：`backup_restore.py`、`desktop_settings.py`、必要的 `storage_files.py`／`ai/key_store.py`，以及新的 `tests/test_audit_storage_fixes.py`。

- [x] 先写共享父目录下密钥／ZIP ACL、权限失败不发布、历史根 junction 后备份预览／恢复回归；运行并记录预期失败。
- [x] 用最小改动保证写前私有权限，历史身份与实际路径检查区分，保留原安全防护。
- [x] 执行新增与备份／迁移／存储相关回归；180 项通过、1 项权限跳过，规格及质量复核通过。排他创建失败不删除已有文件也已补测。

## 任务二：知识 F01／F05

文件：`knowledge_library.py`、必要的 `candidates.py`、`web/app.py`；新增 `tests/test_audit_content_fixes.py`。

- [x] 先复现人工编辑后重存、写后失败回滚、旧 ignore 对已入库／不存在／普通待审／忙态的行为；修正测试装配后确认 14 项真实业务失败。
- [x] 保留编辑候选、新请求身份及正确文件回滚；旧接口在事务和操作锁中进行状态约束。
- [x] 新增 17 项及相关 208 项通过；规格、质量各独立复核 34 项通过。源码真实浏览器 50 次页面检查及重存／拒绝旧忽略流程通过。

## 任务三：通知／采集／模型 F02／F06／F07

文件：`pipeline.py`、`collector/ntqq.py`、`ai_usage.py`、`config.py`、`web/ai_usage_routes.py`，以及 `backup_restore.py`／`desktop_settings.py` 中已验证同根因的归档 URI；新增 `tests/test_audit_ingestion_fixes.py`。

- [x] 先写通知入队失败重试／部分收件人／成功去重／禁用、编码源路径、模型长度边界回归；确认 24 项业务失败，单价 API 补修另确认 2 项失败。
- [x] 在报告复用中幂等补通知并保留终止失败预算、延迟、租约与成功，统一七处 URI 和包括 API 的 200 字符限制。
- [x] 新增共 49 项回归，专项先后 226 项／补修后 172 项通过；规格及质量复核通过，质量独立 102 项通过。

## 任务四：整体验证和交付

- [x] 更新后台标识，最终 pytest 1572 passed／2 skipped，38 个 CJS 通过；源码与实际隔离 EXE 各 50 次页面扫描，另完成长模型与单价／计费流程。
- [x] 正式包 111 模块／51 模板／完整性清单通过，合成与真实验证边界已记录。
- [x] 更新审查状态、路线图及[验收](../specs/2026-10-17-audit-remediation-verification.md)，合入主项目后 72 项回归通过；桌面入口及 current 已切换，旧 VersionList 保留回退，原配置／既有文件哈希不变。

Python 一律 `D:\CodexTools\python\Scripts\python.exe -X utf8`；所有验证日志写入证据目录。不得改真实数据或停止 Codex。
