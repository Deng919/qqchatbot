# 按群读取图片与文件验收（2026-10-07）

已实现默认关闭的群聊图片／文件独立开关，开启后自动参与新摘要和问答。原始消息正文保留，附件结果单独缓存；群详情提供结果、重读、来源聊天上下文。关闭开关后不向新摘要／问答提供附件缓存，历史报告保留。

## 验证

- 全量回归：981 项通过、1 项 Windows 符号链接权限跳过。日志：`D:/Cache/QQDigestAttachments-2026-10-07/pytest-final.log`。此后补充 4 项密钥／协议测试，附件专项共 30 项，均使用合成来源和模拟 AI。最后同时重跑附件专项及首次使用的 19 项回归，49 项通过。
- 20 个 Node 行为脚本通过，包括过期响应、重复读取、关闭后的返回、纯文本展示及原有连续上下文。日志：同目录 `node-final.log`。
- 独立只读复审完成。修复首次导入配置引用、混合消息附件检索、失败重试轮换，以及路径更新／源库暂不可读时缓存失效与错误下载提示；对应反例先失败后通过。
- 浏览器实际 HTTP 页面：选择一个合成群开启，另一个群保持关闭；保存、文件正文、模拟识图结果、缺失下载提示、缓存再次打开、源消息连续上下文均通过。桌面及 390×844 手机宽度可用，浏览器未见控制台错误。截图：同目录 `attachments-desktop.png`、`attachments-mobile.png`。
- 实际本地 QQ 数据仅用于只读元数据形状验证：图片字段为 45402 文件名／45406 MD5，标准原图目录为 `nt_qq/nt_data/Pic/YYYY-MM/Ori/MD5.ext`。未读取聊天附件发送给模型，未启用真实群。
- 图片请求固定 DeepSeek Flash，并核验官方 API 地址、已配置密钥来源（包括设置页保存的本地密钥）；不从本机 Codex 账号池自动获取密钥。普通文件在本地提取，扫描 PDF 页面使用同一识图请求。

## 当前范围

JPEG/PNG/GIF/WebP；PDF、DOCX、XLSX、PPTX、TXT/MD/CSV/JSON/LOG。单附件 16 MiB、正文 12000 字符、图片单边 8192 像素、PDF 前 10 页、每批 20 个。Office 仅正文、表格、幻灯片文字；不解析其中的内嵌图片，不转写语音，不执行文档脚本，不下载远端链接。标准账号缓存之外的图片需消息中保留可用本地路径。列表／重读覆盖最近 200 条附件消息，最多展示 100 个附件。超限、失败、截断可见，不阻断正常摘要。

本轮没有实测真实 DeepSeek 识图质量，也未替用户启动新版真实桌面窗口。

## 本机交付

- 程序：`D:/Apps/QQDigestDesktop-2026-10-07-Attachments/QQDigestDesktop.exe`
- 后端标识：`2026-10-07-attachments`；45 个模板逐字核对、11 个模块编译对象核对；Pillow 与 PyMuPDF 原生库已打包，发布清单完整性通过。
- 桌面入口：`D:/Desktop/QQ Digest.lnk` 已指向新版。
- 回退：`D:/Apps/QQDigestDesktop-2026-10-07-FailureIgnore`。数据快照：`D:/Cache/QQDigestAttachments-2026-10-07/archive-before-upgrade.sqlite`；部署记录：同目录 `deployment-verification.json`。
- 程序仍使用 `D:/Desktop/AI/chatbot/config/config.yaml` 和原有数据目录。旧窗口须退出后从桌面入口重开才能加载新版后端。公开版 0.2.0-beta.1 安装包未更新，未上传 GitHub。
