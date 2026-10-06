# QQ Digest Windows 公开测试版

公开版仅支持 **DeepSeek API**，请使用自己的 API Key。安装包不包含账号、密钥、默认密码、聊天记录或开发者配置。调用 API 可能产生 DeepSeek 费用。默认模型为 `deepseek-flash`，可在 AI 设置中修改。

## 安装与首次使用

1. 从 GitHub 预发布页面下载 `QQDigest-0.2.0-beta.1-Windows-x64-Setup.exe` 和 `.sha256`。用 `Get-FileHash <安装包路径> -Algorithm SHA256` 核对哈希。
2. 系统需要 Windows x64，以及 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。安装程序不会下载依赖，也不会自动启动应用。
3. 选择尚不存在的程序目录和数据目录；两个目录必须分开，不能互相包含。输入并确认至少 8 个字符的登录密码。已有目录不会被覆盖。
4. 打开 `QQDigestDesktop.exe` 或桌面快捷方式，用刚才设置的密码登录。在首次设置或 AI 设置中填入自己的 DeepSeek API Key。官方地址是 `https://api.deepseek.com`。

有 D 盘时，程序默认安装到 `D:\Apps\QQDigestDesktop-0.2.0-beta.1`；没有 D 盘时安装到 `%LOCALAPPDATA%\Programs\QQDigestDesktop-0.2.0-beta.1`。数据默认保存在 `%LOCALAPPDATA%\QQDigest\Data`，包含 `config/config.yaml`、`session-secret.txt`、归档及日志。数据目录的访问权限仅授予当前用户、SYSTEM 和管理员；AI Key 通过首次设置或 AI 设置保存到数据目录中的密钥文件；具体路径由配置决定。程序目录中的 `launcher.json` 指向独立的数据配置。

升级或卸载前请备份数据。卸载时关闭应用，删除程序目录和桌面快捷方式。只有确定不再保留历史记录时才删除数据目录。此测试版尚未进行代码签名，Windows 可能显示未签名应用提示。若桌面界面无法启动，请先确认 WebView2 Runtime 已安装。

## 问题反馈

请到 [GitHub Issues](https://github.com/Deng919/qqchatbot/issues) 提交问题或建议，附版本、Windows 版本、复现步骤和相关错误文字。附件中请删除 API Key、会话密钥、密码、QQ 号码和私聊内容，不要上传整个数据目录。

## 从源码构建安装程序

先构建全新的公开版程序包，再运行：

```powershell
D:\CodexTools\python\Scripts\python.exe scripts/build_installer.py
```

默认读取 `D:\Apps\QQDigestDesktop-0.2.0-beta.1`；编译中间文件位于 `D:\Cache\QQDigestPublicBeta-2026-10-06\installer`。安装包与 SHA256 文件输出到 `D:\Downloads`。使用 Windows .NET Framework 自带的 `csc.exe` 编译。构建器校验公开版标记、版本兼容字段及完整文件清单，拒绝带启动配置或密钥文件的程序包。

安装程序提取前会校验内嵌 ZIP 的 SHA256，并拒绝目录穿越、符号链接及目标目录冲突。安装过程使用进程间互斥锁，避免同时运行多个安装程序覆盖彼此目录。测试入口 `--test-install PROGRAM DATA` 从标准输入读取合成密码，不创建快捷方式或注册表项；仅对全新的测试目录使用。
