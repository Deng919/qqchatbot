# QQ Digest Core Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐群配置、空消息处理、失败可见性和 QQ Bot 长连接审核闭环。

**Architecture:** 保持 SQLite 为运行时权威来源，在现有 API、prompt、pipeline 和 Bot 模块上做小范围贯通。每项以行为测试驱动，不改数据库主结构，不引入新服务。

**Tech Stack:** Python 3.11、FastAPI、SQLite、Jinja2/JavaScript、pytest、websockets

---

### Task 1: 群配置贯通

**Files:** `tests/test_web.py`, `tests/test_summary.py`, `qq_digest/web/app.py`, `qq_digest/web/templates/groups.html`, `qq_digest/summary.py`, `qq_digest/prompt_builder.py`

- [x] 添加 API 字段、校验和 prompt 失败测试并确认失败。
- [x] 实现关键词 JSON 持久化、采集窗口更新和 prompt 注入。
- [x] 增加群组页编辑控件并运行 Web/摘要测试。

### Task 2: 空消息和失败可见性

**Files:** `tests/test_pipeline.py`, `tests/test_web.py`, `qq_digest/pipeline.py`, `qq_digest/web/app.py`, `qq_digest/web/templates/dashboard.html`

- [x] 添加空消息不调用 AI 和 jobs 返回错误的失败测试。
- [x] 跳过空时间窗，安全返回截断错误并在总览展示。
- [x] 运行 pipeline/Web 目标测试。

### Task 3: Bot 中文命令与心跳

**Files:** `tests/test_bot_commands.py`, `tests/test_websocket.py`, `qq_digest/notify/commands.py`, `qq_digest/notify/websocket.py`

- [x] 添加中文命令、批量确认和心跳生命周期失败测试。
- [x] 实现中英文解析、批量结果和可取消心跳任务。
- [x] 运行 Bot 目标测试。

### Task 4: 验证

**Files:** `README.md`

- [x] 更新配置字段和 Bot 中文命令说明。
- [x] 运行全量 pytest、compileall 和 diff check。
- [x] 重启 8765 服务并检查桌面/手机页面、控制台和关键 API。
