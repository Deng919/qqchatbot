# P2-05 Storage Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在已有存储设置中提供真实占用、可预览的清理和用户批准的自动备份／缓存保留规则。

**Architecture:** 文件保护与租约、只读目录扫描、策略存储和清理执行分别放在小模块。DesktopBridge 绑定当前配置及任务互斥，现有小时循环负责调度；独立网页路由和模板展示状态。

**Tech Stack:** Python 3、SQLite、标准库 ZIP/SHA-256、Windows msvcrt / POSIX flock、FastAPI、Jinja、原生 JavaScript、pytest。

**Approved spec:** `docs/superpowers/specs/2026-10-08-storage-management-design.md`。使用现有 `codex/public-beta-2026-10-06` 工作分支，初始工作区干净。只用合成数据执行删除测试。两块无共享文件的基础模块可按 dispatching-parallel-agents 分别开发，控制者整合清理、网页和调度；每块先红后绿，最终独立审查。

## Task 1: 受管临时目录和进程间保护

Files: create `qq_digest/storage_files.py`, `tests/test_storage_files.py`; modify `qq_digest/backup_restore.py`, `qq_digest/refresh.py`。

- [x] 写并运行失败测试，证明跨进程同一根目录锁拒绝并发、不同根目录可并发，活动临时目录不可取得清理锁，无标记目录不认领，正常退出删除目录。测试调用 `root_lock(root)`、`managed_temporary(root,prefix,owner)`、`cache_metadata(path,owner)`、`cache_lock(path)`。
- [x] 实现 `root_lock(root)` 可重入进程间非阻塞锁，冲突抛 `StorageBusy`；目录树路径使用 `safe_path(path,root=None)` 拒绝任意祖先 reparse/symlink，并返回未变更的绝对路径。读操作不隐式创建目录。
- [x] 实现 `managed_temporary(root,prefix,owner)` 上下文返回 Path，写版本化 JSON 标记（owner 的规范绝对路径、UTC 创建时间、prefix），整个生命周期持有 `cache_lock`；正常及 Python 异常退出清理，进程崩溃时锁自动释放，标记留下。仅允许备份、检查、刷新三类前缀。
- [x] 将备份创建与校验包进同一备份父目录 `root_lock`，用 `managed_temporary` 替换两处 TemporaryDirectory；检查缓存 owner 从清单 source_data_dir 获取，旧格式保持正常读取但不自动认领。刷新快照替换 mkdtemp + finally rmtree，并给 refresh_databases 可选 `cache_owner`，调用处将 cfg.data_dir 传入，无 owner 的调用只保留旧清理行为、不生成自动候选。
- [x] 运行新测试及 `tests/test_backup_restore.py tests/test_refresh.py`，检查 git diff；不得更改备份清单、恢复校验规则和 QQ 发布路径。

## Task 2: 策略与只读占用

Files: create `qq_digest/storage_inventory.py`, `qq_digest/storage_policy.py`, `tests/test_storage_inventory.py`, `tests/test_storage_policy.py`。

- [x] 先运行失败测试：`StoragePolicy(archive_path).settings()` 默认 enabled=True, backup_days=30, backup_keep=3, cache_days=7, revision=0；`update(values,expected_revision)` 严格验证范围及 bool/int，版本不符抛 `StorageConflict`，重启保留值。
- [x] 专用 SQLite 连接创建设置和执行记录表；`record(result,automatic)` 保存最近结果与最后成功自动执行时间；`last_result()` 返回结构，`automatic_due(now)` 根据开关及至少24小时间隔判断。保存设置使用 BEGIN IMMEDIATE，日志无正文及密钥。
- [x] 写占用失败测试，调用 `inventory(config,backup_root,temp_root)`：返回 categories(name,size_bytes,file_count)、total_bytes、disks、warnings、paths；库及 WAL/SHM 同类、分类重叠不重计、无权限和链接警告、缺失目录不创建、QQ 原件不递归扫描。
- [x] 实现标准库 lstat + 非链接递归；仅扫描已知报告／知识／工作快照／备份／日志路径和数据库文件。配置根目录过宽时只纳入有数据库引用的已知内容文件，不把用户目录和盘符根算作程序数据。临时根仅纳入三个受管前缀，不扫描版本状态和下载。设备及 inode 去重；磁盘剩余空间按实际卷去重。
- [x] 新测试通过后向控制者报告返回结构，不修改其他模块。

## Task 3: 预览与受限删除

Files: create `qq_digest/storage_management.py`, `tests/test_storage_management.py`。

- [x] 先写失败测试，使用真实合成备份（复用 test_backup_restore._data），调整 ZIP 清单 created_at 后重建；证明30天与最少3份同时满足、手动／安全／其他owner／损坏ZIP受保护、缓存活动／无标记／过期分别跳过／保护／可清理。
- [x] `StorageManager(config,backup_root,temp_root,now=None)` 使用 policy 和 inventory；`snapshot()` 返回 inventory,policy,last_result；`preview()` 返回 token,expires_at, candidates,protected,estimated_bytes。token 只在服务端内存保存，最多16份，过期600秒，绑定策略版本和当前配置路径。候选含私有 path,root,kind,fingerprint；客户端只能提交token。
- [x] ZIP 元数据严格大小界限、类型、时区、清单kind/文件名前缀/owner匹配；采用 inspect_backup 的完整校验选出最新最低份数，所有可删除ZIP也必须通过校验，失败标为保护。文件 SHA256 和 stat 指纹固定预览身份。
- [x] `clean(token,automatic=False)` 在 root_lock 下重新检查策略、所有保留备份和候选身份、年龄、目录租约；只执行原候选子集。候选改动或保护备份消失时不删除。先同根隔离，用持久状态文件记录原路径和身份；复核成功才删。异常恢复，重启发现隔离记录先恢复原路径；遇到冲突保留并提示。不会扩大清理集。
- [x] 覆盖 symlink、junction、硬链接、过期token、设置冲突、跨进程忙、文件改变、删除失败和隔离中断回归；新测试及基础模块测试全部通过。

## Task 4: DesktopBridge、调度与鉴权接口

Files: modify `qq_digest/desktop_settings.py`, `qq_digest/web/app.py`; create `qq_digest/web/storage_routes.py`, `tests/test_storage_routes.py`；扩展桌面测试。

- [x] 写失败测试：bridge 同配置复用manager；launcher迁移后所有清理操作拒绝；自动备份关闭但清理到期仍执行；忙时不记成功、关闭策略停止自动执行；所有清理接口需登录及桌面桥接。
- [x] bridge 添加 get_storage/update_storage_policy/preview_storage_cleanup/clean_storage/maybe_scheduled_cleanup，按既有 `_claim_operation('storage_mutation')` 与 `_backup_lock` 执行；保存及清理禁止过期launcher配置，snapshot只读文件。
- [x] 新增 GET `/api/storage`、PUT `/api/storage/policy`、POST `/api/storage/preview`、POST `/api/storage/clean`。StrictBool/StrictInt、extra=forbid，token为32位十六进制。冲突409，输入422，存储读取失败503；清理不接收路径。
- [x] 在小时循环中独立调用备份与清理，分别捕获异常，不新增线程。跨重启凭持久最后时间避免重复自动执行；automatic=False不改自动成功时间。
- [x] 路由及桌面相关测试通过。

## Task 5: 设置页面

Files: create `qq_digest/web/templates/storage_management.html`, `storage_management_script.html`; modify `settings.html`; create `tests/test_storage_ui.py`, `tests/storage_ui.cjs`。

- [x] 先写失败模板/Node测试：进入storage才加载、重复提交禁止、未保存策略禁止清理、输入修改作废预览、旧响应忽略、409不覆盖用户草稿、未知占用不作零、列表使用textContent。
- [x] 沿用 api() 和已有 setting-card 样式；用含label的数字字段和enabled复选框，默认30/3/7。渲染占用列表、磁盘剩余、警告折叠、结果状态；候选／保护明细置于可滚动details。预览完成后才启用立即清理按钮。
- [x] 备份页旧固定文案换成策略入口；缺少desktopbridge时禁用本卡并提示桌面版。保存策略不触发立即清理，后台按既定周期执行。
- [x] Node/UI测试通过，在合成HTTP配置的1280/390浏览器检查保存、预览、执行及冲突。QA存放 `D:\Cache\QQDigestStorage-2026-10-08`。

## Task 6: 验证、文档与本机部署

- [x] 按 requesting-code-review 先做需求符合性、再做代码质量独立审查，修复重要问题并回归。审查内容含完整 spec 与本计划。
- [x] `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest -q` 全量通过；运行全部 Node UI 测试、Python AST、Jinja编译及git diff --check。只对新更改／失败重复必要检查。
- [x] 更新 README、ROADMAP 和 `docs/superpowers/specs/2026-10-08-storage-management-verification.md`，记录范围及旧无owner缓存保留限制；保存合成浏览器截图。
- [x] 按既有构建脚本生成 `D:\Apps\QQDigestDesktop-2026-10-08-Storage`，核对新模块和47份模板、manifest及实际EXE合成启动；真实数据仅做只读备份，真实目录不执行清理。再更新本机桌面入口，Usage版保留回退；不发布GitHub或公开安装包。
- [x] 验证后提交代码和验收记录；最终简述用户可使用的入口、默认规则和重启要求。

## Plan review

六项覆盖统计、策略、候选校验、执行、调度、网页、跨进程、失败恢复、回归和本机交付。基础租约与统计模块文件无重叠，其余工作依赖基础模块后顺序整合。所有破坏性验收只针对合成目录；未知缓存保留的限制已写明。
