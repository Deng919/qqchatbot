# Desktop Updates Implementation Plan

> 使用 subagent-driven-development 分工独立版本校验模块，其余交接与 UI 顺序实施；最终只读审查。

**Goal:** 完成本机桌面包切换、失败回退及受管理旧版本清理。

**Architecture:** release.json 和管理清单区分程序、偏好及外部用户数据；独立 worker 等待原进程退出后启动新包；令牌及窗口就绪确认后提交入口。失败停止目标，恢复原入口并启动原版。

**Tech Stack:** Python、FastAPI、Jinja、Windows 进程与快捷方式 API。

## 1 发布校验与清理
- [x] tests/test_desktop_releases.py 先覆盖修改／额外／遗漏文件、路径逃逸、链接、兼容性和保留版本，再验证失败。
- [x] desktop_releases.py 实现清单生成、验证、本机登记、列表和清理；目录必须位于明确根内，不凭目录前缀删除。
- [x] 构建脚本生成不可变程序清单；管理状态放 D:/Cache/QQDigestDesktop/updates。

## 2 交接状态机
- [x] tests/test_desktop_updates.py 覆盖备份失败、原进程占用、目标失败、确认错误、回退及中断恢复。
- [x] desktop_update_worker.py 实现 prepare/run/ack，事务原子写入，校验配置和程序指纹。
- [x] desktop.py 分派 worker 模式，窗口启动回调写确认，失败模式避免阻塞消息框；bridge 保持操作锁直到退出。
- [x] 同兼容性切换保留用户数据；失败停止自己的目标进程，不覆盖旧数据。

## 3 设置入口与接口
- [x] 独立鉴权 routes、模板及 JS；GET 列表与预览，POST 切换／清理严格校验版本 ID 与清单指纹。
- [x] 设置导航直达版本页，浏览器模式提示桌面能力缺失；窗口切换提示简短。
- [x] API 验证登录、互斥、修改指纹与无桥接，浏览器验证宽窄屏及清理预览。

## 4 发布验收
- [x] 只读需求与质量审查，修复实际风险后重验。
- [x] 全量 pytest、Python／模板／JS 语法核对。
- [x] 新包 D:/Apps/QQDigestDesktop-2026-10-06-Updates，合成配置实际 EXE 交接／失败回退；测试不使用真实 QQ 或 AI。
- [x] 登记可信 FirstUse 基线，更新现有快捷方式，保留旧版；更新 README 和 ROADMAP 并提交。

## 关键测试接口约定
`write_release(directory, version, backend_id, compatibility='archive-2026-10-06', worker_protocol=1)` 生成 release.json。
`ReleaseRegistry(root, state_dir)`：register(directory)、verify(version, expected_digest)、snapshot(current, data_paths)、clean(version, expected_digest, protected, data_paths)。
版本记录必须含 version/path/backend_id/compatibility/worker_protocol/digest/size_bytes。清理不能修改 registry 以外的目录，当前/rollback/worker/运行/数据路径受保护。完整清单在每次执行前复核。

worker 事务包含 source/target、配置指纹、原入口、备份、状态、随机 token；确认文件位于同一状态目录且与 token/backend/config 匹配。所有合成文件放 D:/Cache/QQDigestDesktopUpdates-2026-10-06。


## 验收记录
- 全量：846 passed、1 skipped、1 warning，261.12 秒，退出码 0。日志 D:/Cache/QQDigestDesktopUpdates-2026-10-06/final-full-pytest.log。符号链接创建缺少 Windows 权限而跳过，实际 junction 测试通过。
- 在全量后补充旧 worker 结束回调不能释放新操作的锁归属检查；34 项相关测试通过，最终 EXE 嵌入代码包含 _update_guard。未重复全量，针对这项局部修改复验互斥及版本 API。
- 80 Python 模块、41 Jinja 模板、16 个渲染页面和 125 段 JavaScript 语法检查通过；最终包 41 模板逐字节核对，4 新模块均在 PYZ 中。
- 独立只读审查修复：并发写事务、回退未确认、PID 复用、停止目标失败时仍启动第二个写入者、取消误覆盖旧事务；末次复审无遗留问题。跨进程清单锁及操作锁归属分别验证。
- 浏览器：设置直达、版本列表、清理预览、回退预览、取消、390 窄屏无横向溢出，未发现控制台警告或错误。截图 browser/desktop-list.png、mobile-preview.png、final-actual-exe.png 位于验收缓存。
- 实际 EXE：成功切换到合成包 B、切回源包、无效 Windows EXE 启动失败后确认原窗口及服务恢复；3 次有效数据备份、合成配置逐字节不变、1 条归档消息保留。记录 exe-qa/verification.json。
- 实际旧版：切到 FirstUse 协议 0 包，核对可见窗口和预期后台／配置，消息和配置保留。最终锁归属修改后重新打包，并再次使用实际 EXE 验证此流程。记录 legacy-qa/verification.json。
- 测试包 D:/Apps/QQDigestDesktop-Updates-QA-B、QQDigestDesktop-Updates-QA-Bad 已由管理器清理；两个发布包测试 launcher／偏好、管理清单与事务状态均恢复。合成 ZIP 移至 exe-qa/backups、legacy-qa/backups；不在用户备份列表保留测试数据。
- 真实 QQ 刷新、真实 AI 与外部通知均未用于验收。FirstUse 无版本管理页属于初始基线限制，README 和设计记录说明重新运行保留的新 EXE。
