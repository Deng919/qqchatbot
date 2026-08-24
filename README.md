# QQ Digest

个人 QQ 群消息摘要、重点信息审核和 Markdown 知识库工具。

## 当前阶段

MVP 提供本地归档、每日摘要流水线、Web 审核、Markdown 入库和基础 CLI。真实 QQNT 数据库适配与官方 QQ Bot 通知在后续阶段实现。

## 安装

```powershell
python -m pip install -e ".[test]"
```

## 初始化

```powershell
Copy-Item config/config.example.yaml config/config.yaml
qq-digest hash-password "你的密码"
```

把输出的散列写入 `config.yaml` 的 `security.web_password_hash`，再设置：

```powershell
$env:QQ_DIGEST_SESSION_SECRET="随机长字符串"
$env:QQ_DIGEST_AI_API_KEY="你的API密钥"
```

## 检查

```powershell
qq-digest doctor
```

## 测试

```powershell
python -m pytest
```

## 每日运行

MVP 使用一次性命令而不是常驻调度循环。Windows Task Scheduler 负责触发，Python 只负责当天业务逻辑：

```powershell
qq-digest run-daily --config-path D:\Desktop\AI\chatbot\config\config.yaml
```

创建每天 22:00 运行的计划任务（需要把命令、配置和日志路径替换成实际安装路径）：

```powershell
$action = New-ScheduledTaskAction -Execute "<python安装路径>\Scripts\qq-digest.exe" -Argument 'run-daily --config-path D:\Desktop\AI\chatbot\config\config.yaml' -WorkingDirectory "D:\Desktop\AI\chatbot"
$trigger = New-ScheduledTaskTrigger -Daily -At 22:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "QQ Digest Daily" -Action $action -Trigger $trigger -Settings $settings
```

计划任务需要能读取 `QQ_DIGEST_AI_API_KEY` 和 `QQ_DIGEST_SESSION_SECRET`。建议用 Windows 用户环境变量或按服务账户安全策略配置；不要把密钥写入 `config.yaml`。Web 审核页用 `serve` 启动，默认监听局域网 `0.0.0.0:8765`，手机和电脑必须在同一局域网，公网访问留给后续 VPN 方案。

## 安全边界

- 不修改原始 QQ 数据库。
- API key 和 QQ Bot 密钥只来自环境变量。
- Web UI 面向 localhost/局域网，不暴露公网。
- Web 密码使用 PBKDF2-HMAC-SHA256，120,000 轮；后续可按算法前缀升级 Argon2/bcrypt。
