# 摘要与后台恢复验收

日期：2026-10-14。基线 `a0d57d6`，修复提交 `45a7ddb`；开发分支 `codex/summary-recovery-20261014` 已 fast-forward 合入主工作区。

## 修复结果

| 复现问题 | 最终行为 | 主要回归 |
| --- | --- | --- |
| 首次进度 GET 401 时未知任务 ID，登录后不再读取 | 登录恢复后重新读最近进度；已知在途任务继续原 GET 查询，不重放生成 POST | `summary_progress.cjs`、浏览器与隔离 EXE |
| 已打开范围面板，迟到恢复只改日期／偏好字符串 | 同步挂载控件的实际群选择、计数和时间选项，作废旧预览；用户新输入和预览保留 | `summary_restore_edges.cjs`、真实浏览器 |
| 旧恢复返回抢先写 ID，较新请求被忽略 | 递增恢复 token 只采最后一次读取；旧错误及用户新任务有状态保护 | 同上及 `summary_progress.cjs` |
| 恢复历史日期仍显示“今天” | 开启面板和恢复共用 `rangeTimeMode` 的日期推断，并刷新日期提示 | 组合模板 today/week/custom 回归 |
| 成功读到终态任务后仍显示登录过期 | 当前成功结果采用前清除旧连接提示，也覆盖无最近任务结果；旧请求不能清除新任务反馈 | 组合模板及隔离 EXE |
| 日报／补生成／采集一次普通异常永久结束周期任务 | 周期边界捕获普通异常、更新已有内存失败状态并记录诊断；按原周期继续；决策读取失败也可恢复 | `test_summary_recovery_background.py`、`test_web_background_tasks.py` |

后台保留原 60 秒日报检查、采集间隔、持久化冷却／次数预算、OperationBusy 及取消语义。连续普通失败不忙循环；CancelledError 继续退出；取消等待实际 worker 完成后才释放锁。没有伪造 jobs、推进成功水位或增加后台重启／统一重试框架。`last_result` 的失败反馈沿用内存状态，不新增跨重启补偿策略。

本轮另定点复查知识保存、报告调用和 AI／采集组合边界，未依据未经复现的假设修改其他业务。

## 验证

- 既有基线专项 **35 passed**。
- 首次读取、选群同步和旧恢复返回均有 RED；后台首轮 **14 failed、26 passed**，修复后 **40 passed**，关联 worker／scheduler 共 **55 passed**。
- 相关 Python 组合专项 **64 passed**；独立复核后台 40 项及补生成持久化预算 4 项通过。两处显示问题分别 RED→GREEN，已纳入永久实际模板组合测试；最终独立 UI pytest **5 passed**。
- 最终全量 **1453 passed、2 skipped、1 warning，退出 0，514.55 秒**。两项跳过为 Windows 创建符号链接权限；警告为现有 Starlette/httpx 弃用提示。
- **37 个 CJS 脚本通过**；真实 generation/progress 模板与 GroupSelector state 组合验证旧预览作废、用户编辑保留、时间选项、终态提示、新任务优先及旧响应保护。
- Chromium 在 1280×820、390×844 各执行首次登录恢复、终态恢复、迟到范围、用户新输入、重复恢复等五个场景，共 **10 个场景通过**，生成 POST=0，无 pageerror 或横向溢出；已查看截图。进度响应是合成替身，数量预览与登录使用实际本地接口。
- 隔离实际 EXE：健康、backend id、候选登录草稿／入库撤销、消息浏览、上一轮待办保存／越界保护，以及本轮首次 401→重新登录→终态恢复／提示清除／时间选项均通过，生成重放=0，无 pageerror。
- 隔离与正式包均核对 **110 个模块、50 个模板**与构建源码一致，文件清单和哈希校验通过；CLI 由源码测试覆盖。
- 合并后再次通过 **57 项摘要／后台／桌面专项和全部 37 个 CJS**。

浏览器夹具最初用精确首页 URL 等待、JSON 方式登录、随后对故意扣住响应的恢复 Promise 自动等待，导致验收脚本等待或断言失败；分别改为 DOM 就绪、表单编码、非等待调用后通过。只终止了命令行与绝对脚本路径匹配的本轮浏览器辅助进程，没有据此改产品源码或停止 Codex。

证据根 `D:\Cache\QQDigestSummaryRecovery-20261014`：`baseline.log`、`*-red.log`、`background/verification.md`、`review/review-report.txt`、`targeted-green.log`、`full-pytest.log/.exitcode`、`node-final.log`、`browser-results.json`、`late/dirty/race-*.png`、`package-smoke.json`、`package-integrity-*.json`、合并后回归与 `activation-results.json`。pytest 与独立 CJS 可能重叠，不累计为独立功能数。

## 交付和边界

桌面入口已切换到 `D:\Apps\QQDigestDesktop-2026-10-14-SummaryRecovery-Release`，backend id `2026-10-14-summary-recovery`；TaskIntegrity-Release 保留回退。快捷方式目标与工作目录已独立读取确认，入口／版本登记原稿位于 `activation-backup`。切换工具退出 0，有 COM 对象释放提示，后续登记和快捷方式验证成立。

沿用 `D:\Desktop\AI\chatbot\config\config.yaml` 及生产数据，配置和三个既有未跟踪文件哈希未变。正式生产配置未再次启动，退出旧 QQ Digest 窗口后重开桌面入口生效。隔离包为 `D:\Apps\QQDigestDesktop-2026-10-14-SummaryRecovery`，合成配置、数据和辅助脚本在证据缓存。公开安装包未更新。

未调用真实 QQ 解密／采集、AI 本地桥接、DeepSeek、识图、QQ Bot 投递、原生通知或开机自启。后台恢复依靠确定性故障注入验证，本轮没有实机制造磁盘故障或真实服务宕机。未修改 Codex 应用或设置；P2-07 按群云 AI 权限仍是后续计划。
