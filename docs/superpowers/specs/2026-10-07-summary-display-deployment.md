# 摘要显示修复部署记录

用户再次看到标题 `**` 和正文 `〔原消息：…〕`。核对正在运行的 PID 22412，程序路径为 `D:\Apps\QQDigestDesktop-2026-10-06-Updates\QQDigestDesktop.exe`，监听本地 8765。安装目录的 summary_reading_script.html 仍为旧版本，没有 summaryCleanText；源码此前已经修复，部署遗漏导致截图中的旧显示继续出现。

此次只将经过验证的摘要显示模板更新到实际运行目录 `_internal\qq_digest\web\templates\summary_reading_script.html`。差异仅为显示文本清理及其调用，保留原报告和结构化来源。未替换 EXE 或后端，未部署全文索引和归档问答功能，未重打包公开安装包。

修改前确认原版本文件清单完整、登记匹配、没有在途更新。在版本登记互斥锁内备份模板、release.json 和 registry.json，原子替换模板并刷新文件清单及该版本的登记摘要；失败自动恢复备份。修改后核对安装模板与源码字节一致，完整版本校验通过。

备份和验收脚本：`D:\Cache\QQDigestSummaryDisplay-2026-10-07`。包括 summary_reading_script.before.html、release.before.json、registry.before.json、deploy.py 和 qa_server.py。备份登记文件只用于审计或整组恢复，不能在后续版本登记变化后直接覆盖当前状态。

验证：

- tests/summary_clean_display.cjs 支持传入安装模板路径，增加截图同型标题及消息编号回归。原安装模板运行失败，更新后与源码模板均通过。
- tests/test_find_experience.py、tests/test_summary_reading.py：37 passed，1 条已有 Starlette/httpx 警告。
- 独立本地 HTTP 验收直接读取修改前备份与实际安装模板，浏览器分别确认旧版保留星号/编号、新版移除；1280 和 390 宽度检查，无横向溢出、无控制台错误。
- 当前桌面进程保留运行，已打开页面仍含旧 JS，须刷新窗口或退出重新打开后加载更新。没有验证用户已刷新后的真实报告窗口，不读取真实归档或密钥。
