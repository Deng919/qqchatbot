# codexID ChatGPT Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 QQ 摘要在本机优先使用 `codexID` ChatGPT 账号池，并在 GPT 不可用时回退现有兼容接口。

**Architecture:** PowerShell 包装器复用已有 Cockpit 账号池和临时认证文件机制调用 `chatgpt-bridge chat`。Python 增加 bridge client、fallback client 和统一 factory，CLI/Web/调度全部通过 factory 构造 AI。

**Tech Stack:** PowerShell 5.1、Node.js `chatgpt-bridge`、Python 3.11、Pydantic、pytest

---

### Task 1: 文本账号池包装器

**Files:**
- Modify: `D:\CodexTools\gpttoimage-session\CockpitImage.psm1`
- Modify: `D:\CodexTools\gpttoimage-session\CockpitPool.psm1`
- Create: `D:\CodexTools\gpttoimage-session\chat-current-account.ps1`
- Create: `tests/test_chatgpt_bridge_wrapper.py`

- [ ] **Step 1: 编写失败测试**

测试用伪 Cockpit 账号和伪 Node 可执行文件运行 `-DryRun` 与一次文本调用，断言账号被发现、输出 JSON 不含凭据、临时认证文件已删除。

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_chatgpt_bridge_wrapper.py -q`
Expected: FAIL，因为 `chat-current-account.ps1` 尚不存在。

- [ ] **Step 3: 实现最小文本调用链路**

新增 `Invoke-CockpitBridgeChatProcess`、`Invoke-CockpitSelectedAccountChat`、`Invoke-CockpitPoolChat` 和入口脚本；沿用 `CHATGPT_BRIDGE_AUTH_FILE`、刷新回写及清理逻辑。

- [ ] **Step 4: 运行包装器测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_chatgpt_bridge_wrapper.py -q`
Expected: PASS。

### Task 2: Python provider 与自动回退

**Files:**
- Create: `qq_digest/ai/bridge.py`
- Create: `qq_digest/ai/factory.py`
- Modify: `qq_digest/ai/__init__.py`
- Modify: `tests/test_ai_client.py`

- [ ] **Step 1: 编写失败测试**

覆盖请求文件编码、bridge JSON 解析、GPT 成功不回退、GPT 失败回退和双重失败错误聚合。

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_client.py -q`
Expected: FAIL，因为 bridge/fallback 类尚不存在。

- [ ] **Step 3: 实现客户端与 factory**

`BridgeAIClient.chat()` 通过参数数组启动 PowerShell；`FallbackAIClient.chat()` 仅捕获 `AIError` 后回退；`build_ai_client()` 根据优先级构造组合。

- [ ] **Step 4: 运行 AI 测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_ai_client.py -q`
Expected: PASS。

### Task 3: 配置和所有运行入口

**Files:**
- Modify: `qq_digest/config.py`
- Modify: `config/config.example.yaml`
- Modify: `config/config.yaml`
- Modify: `qq_digest/cli.py`
- Modify: `qq_digest/web/app.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: 编写配置和入口失败测试**

断言默认 provider 顺序为 GPT→compatible、路径必须绝对、CLI/Web 使用统一 factory。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_config.py tests/test_cli.py tests/test_web.py -q`
Expected: FAIL，因为新配置和 factory 接线尚不存在。

- [ ] **Step 3: 接入配置与入口**

新增 bridge 配置段和校验，用 `build_ai_client(config)` 替换三个现有 `AIClient(...)` 构造点。

- [ ] **Step 4: 运行目标测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_config.py tests/test_cli.py tests/test_web.py -q`
Expected: PASS。

### Task 4: 全量验证和服务重启

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新本地 GPT、回退和服务器配置说明**
- [ ] **Step 2: 运行包装器 dry-run，确认只输出非敏感统计**
- [ ] **Step 3: 运行全量测试**

Run: `D:\CodexTools\python\Scripts\python.exe -m pytest -q`
Expected: 全部 PASS。

- [ ] **Step 4: 重启 8765 服务并验证 `/api/stats` 和摘要入口**

确认服务响应、自动采集线程存活，且 bridge 失败时摘要任务能回退。
