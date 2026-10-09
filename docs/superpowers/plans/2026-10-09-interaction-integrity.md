# Interaction Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 修复已批准的六项高优先级交互缺陷。

**Architecture:** 保留已有服务分层。API 数据契约补全、异步响应按请求版本验收，待办并发更新使用原子 revision；桌面存储切换使用统一变更屏障。问答只使用明确范围内已有证据。

**Tech Stack:** Python / FastAPI / SQLite / Jinja / 原生 JavaScript / pytest / Node / Playwright。

## Task 1: 群附件设置往返

Files: `qq_digest/web/app.py`, `qq_digest/web/templates/group_details.html`, `tests/test_web.py`, `tests/group_details_async.cjs`。

- [x] 添加往返回归：两个读取开关开启后 GET 返回 true，修改分类不关闭；缺失字段时不提交 false。
- [x] 运行测试确认当前实现失败。
- [x] 接口字段和保存契约：
```python
{"read_images": bool(group.read_images), "read_files": bool(group.read_files)}
```
```javascript
if (typeof group.read_images === 'boolean') patch.read_images = checkbox.checked;
```
- [x] 使用 `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_web.py -q` 和 `node tests/group_details_async.cjs` 验证。

## Task 2: 恢复预览来源一致

Files: `qq_digest/web/templates/settings.html`, new `tests/restore_preview_async.cjs`。

- [x] 用延迟 Promise 测试 A 预览期间改成 B，A 返回后不能恢复。
- [x] 运行 Node 测试确认失败。
- [x] 绑定请求 token、输入路径、校验返回来源：
```javascript
var epoch = ++restorePreviewEpoch;
if (epoch !== restorePreviewEpoch || input.value.trim() !== path) return;
```
- [x] 恢复时再次检查输入快照并冻结来源、目的地和确认选项，失败解锁。
- [x] `node tests/restore_preview_async.cjs` 并使用 Chromium 拦截恢复请求验证，不实际操作备份。

## Task 3: 待办并发保护

Files: `qq_digest/archive.py`, `qq_digest/task_inbox.py`, `qq_digest/web/task_routes.py`, `qq_digest/web/templates/tasks.html`, `tests/test_task_inbox.py`, new `tests/tasks_concurrency.cjs`。

- [x] 添加两窗口状态／编辑及旧库升级测试，先跑失败测试。
- [x] 状态 API 接受仅 `{status: 'done'}`；正文编辑接受 `expected_revision`，原子写入：
```sql
UPDATE tasks SET title=?, owner=?, due_date=?, status=?, revision=revision+1
WHERE task_id=? AND revision=?
```
- [x] 409 时保留输入并提示重新读取；不要自动覆盖。
- [x] `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_task_inbox.py -q`，Node 回归及接口测试。

## Task 4: 等待存储切换屏障

Files: `qq_digest/operations.py`, `qq_digest/desktop_settings.py`, `qq_digest/web/app.py`, `qq_digest/web/templates/base.html`, new focused tests。

- [x] 合成迁移／恢复成功后测试任务、设置变更被 409 拒绝，自动任务不能 claim；读取仍成功。新配置启动恢复可写。
- [x] 运行失败测试。
- [x] 根据桌面桥当前配置与启动配置是否一致判断 pending；协调器 `can_start` false / `claim` 拒绝。统一请求屏障覆盖未使用协调器的写接口；后台通知、提醒等写入也纳入协调器。
```python
if pending_switch and request.method not in ('GET', 'HEAD', 'OPTIONS'):
    return JSONResponse({'detail': '存储位置已切换，请重启程序后继续'}, status_code=409)
```
- [x] 排除登录／退出；同步切换期间禁止其他写操作，检查 GET 路由副作用。全局提示来源于只读状态。
- [x] focused pytest，合成浏览器验证 pending 提示与变更拒绝，不迁移用户目录。

## Task 5: 归档问答只读预览

Files: `qq_digest/archive_qa.py`, `qq_digest/web/templates/archive_qa.html`, `tests/test_archive_qa.py`, `tests/test_archive_qa_web.py`。

- [x] 附件 prepare / QQ 补采 / AI 设为抛异常；存在未读取无关附件时预览仍成功，已缓存相关附件可检索。
- [x] 确认当前实现测试失败，删除预览阶段新读取调用，只使用缓存查询。
- [x] `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_archive_qa.py tests/test_archive_qa_web.py -q`。

## Task 6: 报告版本证据边界

Files: `qq_digest/report_qa.py`, `qq_digest/report_regeneration.py` if shared boundary helper needed, `tests/test_report_qa.py`。

- [x] 测试非零点开始、精确截止、包含／排除边界、旧报告回退、版本正文匹配、损坏时间窗拒绝。
- [x] 运行失败测试。
- [x] 通过 `ReportRevisionService` 获取当前不可变版本正文／payload，复用 `report_window` 与边界语义：
```python
messages = (archive.messages_between(group_id, start, end) if inclusive
            else archive.messages_in_window(group_id, start, end))
```
- [x] `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_report_qa.py -q`。

## 集成与交付

- [x] 对六项进行规格检查、独立代码审查；修复评审问题。
- [x] 全 pytest、所有 Node 回归、Python AST 与模板编译；宽／窄屏合成浏览器关键流程检查。
- [x] 构建 `D:\Apps\QQDigestDesktop-2026-10-09-Interactions`，校验嵌入代码／模板和合成数据 EXE 启动。
- [x] 更新审查记录的修复状态，提交确切文件，保留用户未跟踪模板。
- [x] 只读备份真实数据库后更新桌面快捷方式，保留 Storage 版本回退，并报告验证结果与实际路径。
