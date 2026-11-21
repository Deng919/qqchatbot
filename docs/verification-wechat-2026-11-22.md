# 微信群整理验证（2026-11-22）

## 自动验证

- 回归170项通过（79.698秒，0失败/0错误）：采集、组批处理、同步、历史检查、配置、日报管线、应用组装、MyCortex、后台调度、摘要预览和手动摘要。
- 最终微信专项14项通过（8.351秒），包含HTTP摘要生成并创建候选、消息收藏、手动MyCortex保存与来源标题、仅微信采集/检查、绑定冲突隔离、上游暂停后补写、混合采集QQ失败时微信继续。专项先复现摘要422、补写漏导、缺源导致应用失败和来源键唯一约束异常，再验证修复。
- 独立审查复现并修复HTTP入口、迟到记录、仅微信来源及约束冲突，最终聚焦复审无新增重要问题。审查只用合成数据；模型、落盘和MyCortex的审查验证使用替身。

命令（固定Python环境）：

```powershell
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_wechat_collector.py tests/test_application_runner.py tests/test_group_batch.py tests/test_sync.py tests/test_history_inspection.py tests/test_config.py tests/test_pipeline.py tests/test_mycortex_delivery.py tests/test_web_background_tasks.py tests/test_summary_generation.py tests/test_manual_summary.py -q
D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_wechat_collector.py -q
```

170项回归时微信专项有11项，随后新增2项完整整理测试及1项来源键冲突测试，最终14项独立通过；不能把两轮相加为184个不同测试。

## 浏览器和桌面

- 独立Chrome/Playwright以合成微信来源检查1280px与390px：群列表微信标记、群详情、消息浏览、关键词查找、消息收藏、摘要选择及消息数量预览；无pageerror。截图和脚本在 `D:\Cache\QQDigest-WeChat-20261121\browser-final`，未截真实群内容。
- 新桌面构建成功，程序 `D:\Apps\QQDigestDesktop-2026-11-22-WeChat-Release\QQDigestDesktop.exe`，SHA256 `bb47de0a58f8657ac79f84a97efbe77a8e881271b6c2e6c7534dc097d918d417`。桌面快捷方式已切换，核对程序进程和8765端口归属、healthz=ok。
- 安装包真实API验证：微信群列表和条数可读、MyCortex自动保存关闭、范围摘要预览162条、已生成报告可读取（均返回200）。首次预览曾返回409任务忙，后续同一构建重试为200，未修改代码或重启程序；没有把该暂时返回记为已修复的软件缺陷。

## 真实本地数据

- 修改配置前，将配置与主库一致快照备份至受限 `D:\Cache\QQDigest-WeChat-20261121\before-wechat`；现配置和数据保持Git忽略。
- 只读指定微信归档并正式导入1731条，再次增量同步新增0条。源库/目标库数量一致；所有微信记录sender_qq为空且平台为wechat。原QQ消息按全字段顺序哈希与备份完全一致。
- 使用原Cockpit真实生成一个自然日的范围摘要，输入162条，成功保存1份报告及1条资料候选；31个实际引文ID均可在该微信群归档找到，候选绑定同群。脱敏结果 `live-summary-verified.json`。没有修改审核状态或向MyCortex提交真实群内容。
- 首次较大自然日含1183条，分段模型调用后遇到Cockpit502，未发布不完整报告；随后较小日期生成成功。大范围摘要仍受模型服务稳定性和现有上下文/引文校验限制，不能以较小日期成功视为大范围成功。
- 微信源采集60秒，Digest导入沿用原调度；自动摘要与手动整理使用现有配置。媒体正文和真实昵称未解析，手机独有历史和上游未归档记录不在本接入范围。

## 相邻聊天

用户要求微信/个人QQ小号默认静默，精确纯文本开启/关闭；相邻项目已部署，并有worker/状态/命令/多平台合成测试。其真实发送未在本轮触发，验证依据为相邻项目 `docs/verification.md`，不将历史自动回复测试写作本轮聊天开关实测。
