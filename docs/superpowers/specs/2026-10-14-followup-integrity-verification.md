# 继续检查与优化验收：待办完整性

日期：2026-10-14。基线 `0e4e561`，修复提交 `3144051`，分支 `codex/followup-integrity-20261014`；已 fast-forward 合入主工作区。

## 本轮确认并处理的问题

| 问题 | 根因与最终行为 | 证据 |
| --- | --- | --- |
| 保存等待期间丢失新草稿 | 提交 A 后输入 B，原成功回调删除整个草稿；现在只清除与提交快照相同的输入，其余保留，并采用本次成功返回的版本。后续列表更高版本不会静默重基。列表重建仍反映该任务在途保存，不能重复提交 | `tests/test_content_ui.cjs`、真实 Chromium、实际隔离 EXE |
| 重建后的迟到冲突覆盖新输入 | 旧表单 409 回调读取脱离页面的字段并覆盖 Map，核对按钮只显示在旧表单；现在标记当前草稿冲突、按任务 ID 显示当前卡片入口 | 同上、独立 `review/late-conflict-review.cjs` |
| 旧创建结果关闭新来源面板 | 创建回调无条件关闭面板并改地址；现在捕获提交来源，只有请求 token 对应当前面板时才关闭／改变地址和视图，实际创建成功仍反馈并刷新待办 | 同上、独立 callback matrix |
| 待办整数越界触发服务异常 | revision、任务路径、原消息群号可进入 SQLite 越界绑定；API 在字段／路径校验时返回 422，直接服务调用也拒绝越界 revision。原 signed 整数范围和数字转换兼容保留 | `tests/test_followup_task_contracts.py`，8 类失败回归修复前触发真实 OverflowError |

优化采用已有 taskDrafts/taskPanelToken、一个按 task_id 记录真实在途保存的集合和固定字段比较，不新增表单框架、数据库结构或功能开关。

本轮还定点复查知识发布事务、采集刷新／回滚、摘要进度与登录、候选编辑禁用行为及相关后台组合；未根据未经复现的推断新增改动。这是继续检查中确定问题的修复，不是再次声称所有外部功能实机验收。

## 验证结果

- 既有基线专项：40 passed。
- 修复前真实模板回归确认草稿丢失、旧创建关闭新面板、重建后重复提交；独立复核新增迟到 409 分支后再 RED→GREEN。API 8 类 RED 均触发真实 SQLite OverflowError。
- 相关 Python 专项：149 passed；独立 API／并发复查：28 passed。
- 最终全量：**1439 passed、2 skipped、1 warning，退出 0，504.55 秒**。两项跳过仍为 Windows 符号链接创建权限；警告为现有 Starlette/httpx 弃用提示。
- **36 个 CJS 脚本全部通过**。独立审查另证实成功／500／409迟到响应、临时筛掉任务、来源捕获等五个交叉分支，无剩余可执行审查问题。
- 真实 Chromium：1280×820 与 390×844 分别验证新输入／返回版本、重建时保存保护、当前冲突入口和旧创建保留新来源，均通过；无 pageerror 或横向溢出，已查看宽窄屏截图。首轮脚本未切换“我的待办”而寻找隐藏内容，修正导航后通过，无据此修改产品源码。
- 实际隔离 EXE：健康、backend id、登录过期保留输入、入库／撤销、群消息浏览，以及本轮待办创建→保存 A 等待→输入 B→重建→手动保存 B、越界请求 422 均通过，无 pageerror。
- 隔离与正式包均核对 **110 个模块、50 份模板**与当前源码一致，完整性清单通过。CLI 为源码入口，不在桌面包中。
- 合入主项目后，**54 项待办／边界／并发／桌面专项和全部36个CJS**再次通过。

证据根：`D:\Cache\QQDigestFollowup-20261014`，包括 `baseline.log`、`task-ui*-red*.log`、`validation`、`review/review-result.md`、`targeted-green.log`、`full-pytest.log/.exitcode`、`node-final.log`、`browser-results.json`、`tasks-1280.png`／`tasks-390.png`、`package-smoke.json`、`package-integrity-*.json`、合并后回归日志及 `activation-results.json`。pytest 中调用的 CJS 与独立 CJS 会重叠，不按独立功能数累加。

## 本机交付与限制

正式包：`D:\Apps\QQDigestDesktop-2026-10-14-TaskIntegrity-Release`，backend id `2026-10-14-task-integrity`。桌面 `D:\Desktop\QQ Digest.lnk` 的目标和工作目录已独立读取确认；版本登记 current=TaskIntegrity-Release、rollback=FullAudit-Release。切换工具退出 0，有 COM 对象释放提示，随后快捷方式与登记核对通过。

沿用 `D:\Desktop\AI\chatbot\config\config.yaml`；配置与三个既有未跟踪文件的哈希保持不变。本轮全为合成数据验证，正式生产配置未再次启动；退出旧 QQ Digest 窗口后重新打开快捷方式生效。入口／登记备份在 `activation-backup`。隔离 EXE 包在 `D:\Apps\QQDigestDesktop-2026-10-14-TaskIntegrity`；合成配置、数据和脚本在上述缓存目录；公开安装包未更新。

未调用真实 QQ 解密／消息采集、AI 本地桥接、DeepSeek、识图、QQ Bot 平台投递、通知或开机自启。未修改 Codex 应用、进程或设置。路线图 P2-07 按群云端 AI 权限仍是下一阶段计划，本轮没有将它标为完成。
