# Version list performance implementation plan

**Goal:** 去掉只读列表的历史程序文件扫描，保留操作时的全部安全校验。

**Architecture:** 登记记录和不可变清单匹配用于列表可用性；切换／清理继续即时全量验证。无缓存、线程池或新接口。

**Tech Stack:** Python、FastAPI、现有版本模板与 PyInstaller。

- [x] 在 `tests/test_desktop_releases.py` 增加回归：snapshot 不读取程序内容；登记指纹变化无效；列表允许待校验包但修改／缺失／额外文件阻止 verify／clean。
- [x] 执行 `D:\CodexTools\python\Scripts\python.exe -X utf8 -m pytest tests/test_desktop_releases.py -q -o addopts=''`，确认只读回归因现有全量扫描失败。
- [x] 在 `qq_digest/desktop_releases.py` 增加登记清单匹配检查，只让 snapshot 使用；捕获读取失败以逐项显示无效，保留所有操作路径的 verify。
- [x] 在 `desktop_updates_script.html` 显示“操作前校验文件”／“登记校验失败”，在 `desktop.py` 更新 backend ID，避免复用旧后台。
- [x] 执行版本登记／切换／API／存储／public／桌面打包相关回归、现有 CJS，并同机重测性能；核查完整性操作仍使用 verify。
- [x] 构建隔离合成配置的桌面包，执行宽窄屏浏览器／EXE 检查和源码／模板核对，更新路线图与验收记录，采用快进合入主工作区并更新桌面入口，保留旧版回退。

结果见 [验收记录](../specs/2026-10-16-version-list-performance-verification.md)。
