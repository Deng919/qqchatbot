# QQ Digest Windows 桌面版设计

## 目标

交付可双击运行的 Windows EXE。程序在独立窗口显示现有 QQ Digest 界面，不要求用户打开浏览器或安装 Python。

## 方案

- 使用 pywebview 调用 Windows WebView2 显示现有本地界面。
- 启动时读取外部 `launcher.json` 指向现有 `config/config.yaml`；归档、报告、知识库继续留在原项目目录，避免迁移或复制真实数据。
- 优先连接配置端口上已运行的 QQ Digest 服务；没有服务时，在 EXE 进程内启动同一个 FastAPI 应用。窗口关闭时，仅关闭本进程启动的服务。
- 保留现有登录和数据操作逻辑。窗口保持本地地址，不暴露新的远程端口。
- PyInstaller 以单目录模式打包 EXE、Python 依赖和 Jinja 页面资源。发布到 `D:\Apps\QQDigestDesktop`，构建缓存放在 `D:\Cache\QQDigestDesktop`。

## 错误与验证

- 配置不存在、端口占用、服务启动失败或 WebView2 不可用时，弹出明确错误并退出。
- 测试服务复用、独立启动、退出清理和配置路径解析；运行现有测试套件。
- 实际运行打包后的 EXE，确认窗口加载、登录、页面导航以及关闭后的进程状态。

## 取舍

界面复用网页技术，但用户看到的是独立桌面窗口。当前版本的数据仍依赖现有项目目录；搬迁数据可作为单独迁移任务。
