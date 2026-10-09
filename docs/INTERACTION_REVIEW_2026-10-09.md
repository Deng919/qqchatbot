# 群聊简报交互逻辑审查

日期：2026-10-09。检查基线：`dfd1729`，本机 Storage 版本。目标是找出当前交互的不合理之处，给出可复现的问题和修复顺序。本轮没有修改程序或发布新版。

## 结论与检查范围

主要问题是**页面显示、操作名称与实际执行不一致，以及异步操作没有统一的状态管理**。这会造成开关被覆盖、用户草稿消失、恢复来源错位或阅读结果范围不准确。继续增加功能之前，建议先修复这些流程。

共整理 **29 项问题：6 项 P1、22 项 P2、1 项 P3**。另列 6 项产品设计建议，不混入缺陷数量。P1 指可能覆盖已保存内容、切换后隐藏新增数据，或使发送／问答范围与当前界面不符；P2 指操作闭环、草稿、定位和错误恢复问题；P3 为低影响入口问题。这是交互审查的优先级，不是安全漏洞等级。

- 梳理 104 个 Python 程序文件的模块和 150 个显式路由声明。按摘要与查找、群聊与设置、内容工具三个范围分别审查模板、服务及接口；数字不是功能数量。
- 当前目录有 49 个 HTML 模板，其中 `ai_settings.html`、`catchup.html` 是检查开始前已有的未跟踪旧文件。实际构建清单为 47 份；旧地址已重定向，不把额外文件视为正在启用的页面，也未删除它们。
- 真实 Chromium 检查 21 个页面／入口，在 1280 和 390 像素宽度分别访问，共 42 次页面检查：HTTP 正常，无 JavaScript 页面异常，无页面横向溢出。
- 另外复现群附件开关覆盖、恢复选择错位、AI 用量草稿覆盖、候选编辑未入库、待办旧值覆盖及弹层焦点逃出；执行真实业务函数验证报告截止时间被忽略。恢复验证拦截并模拟提交，没有实际恢复文件。
- 其余异步问题通过模板函数 VM／模拟 API 或确定性源码链路核对。没有调用真实 DeepSeek、QQ 采集、识图、通知或操作用户备份；没有检查真实模型回答质量、Windows 原生选择窗口及网络故障后的全部场景。

证据和复现脚本在 `D:\Cache\QQDigestInteractionAudit-2026-10-09`，包括 `browser-audit.json`、`browser-additional.json`、42 张宽窄屏截图、`route-inventory.json`、官方项目参考内容以及独立合成数据。42 页正常渲染不代表下列操作分支没有问题。

## 优先修复的 6 项

### 01 · P1：已开启的群附件读取会被静默关闭

步骤：开启某群读取图片／文件并保存 → 刷新群页面 → 打开详情 → 只改分类或直接保存。

群列表接口没有返回 `read_images/read_files`；详情把缺失字段当 false，保存时仍提交这两个字段。浏览器验证中，数据库原值都是 1，详情都未勾选，不改附件选项直接保存后数据库都变成 0。

建议：接口返回完整设置；未知状态不能作为关闭提交。读取与保存的字段契约增加实际浏览器回归。

代码：[群读取响应](D:/Desktop/AI/chatbot/qq_digest/web/app.py:1194)、[详情初始化及保存](D:/Desktop/AI/chatbot/qq_digest/web/templates/group_details.html:47)。证据：`browser-audit.json` 的 `attachment_flags`。

### 02 · P1：旧恢复预览可能使用户恢复另一个备份

步骤：选 A.zip → 开始校验 → 请求未返回时将路径改成 B.zip → A 的响应返回 → 填目的地并确认。

输入变化虽然清空预览，旧响应仍无条件写回。页面路径显示 B，恢复请求却使用 A 的预览路径与哈希。哈希可证明 A 未变，不能证明 A 是用户当前选择。浏览器拦截验证显示 B，模拟恢复提交 A。

建议：校验请求使用递增 token，并比较当前输入路径；预览与确认明确显示文件名和路径。恢复期间锁定选择，输入变化作废旧请求结果。

代码：[预览响应](D:/Desktop/AI/chatbot/qq_digest/web/templates/settings.html:296)、[提交路径](D:/Desktop/AI/chatbot/qq_digest/web/templates/settings.html:316)。证据：`browser-additional.json` 的 `restore_choice_race`。

### 03 · P1：完成待办会覆盖另一窗口已经保存的编辑

步骤：A、B 两个窗口打开同一待办 → B 改标题、负责人和日期并保存 → A 点击完成。

完成按钮会提交 A 持有的旧标题、负责人和截止日期；服务没有版本检查。合成 API 验证先保存“新标题”，再提交状态按钮的旧数据，最终标题变回“旧标题”，状态为已完成。

建议：状态操作只更新状态；正文编辑增加 revision，冲突原地显示并保留输入。

代码：[状态按钮](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:37)、[更新服务](D:/Desktop/AI/chatbot/qq_digest/task_inbox.py:174)。证据：`browser-additional.json` 的 `task_stale_status_overwrite`。

### 04 · P1：复制切换存储后仍能写旧目录

步骤：复制并切换成功 → 暂不重启 → 继续采集、保存设置或等待自动任务 → 重启。

复制完成只修改启动配置，运行中的服务仍写原目录。重启会打开复制时的副本，复制后的新增内容没有带过去，在当前视图里消失；原目录仍有这些内容，不能称为物理删除。恢复后等待重启也有类似窗口。此项依据完整源码链路确认，未对用户目录执行迁移。

建议：复制成功进入统一的“等待切换”状态，暂停自动任务并禁止后续写入，提供完成切换入口；或在最终切换前实施可验证的增量同步。

代码：[切换启动配置](D:/Desktop/AI/chatbot/qq_digest/desktop_settings.py:100)、[运行服务使用原路径](D:/Desktop/AI/chatbot/qq_digest/web/app.py:1436)、[成功反馈](D:/Desktop/AI/chatbot/qq_digest/web/templates/settings.html:228)。

### 05 · P1：归档问答预览可能先发送无关附件并产生费用

步骤：群已开启图片／文件读取，日期范围内存在未处理附件 → 点击“查找相关消息”，尚未点击下一步“发送给 DeepSeek”。

后台先取范围内最近 200 条附件消息，调用附件读取，再按问题检索。图片或扫描 PDF 可提前发送到 DeepSeek；被读取附件还可能完全不匹配本次问题，旧附件也可能触发 QQ 源补读。群级读取权限已经开启，问题在于“两步预览”的当前文案没有表达这一阶段的范围与成本。

建议：普通预览只搜索文字和已有附件缓存。需要新识别时，先展示待处理附件、涉及的群及可能调用 AI，再明确执行读取；自动附件处理也需可见的状态与数量。

代码：[预览读取附件](D:/Desktop/AI/chatbot/qq_digest/archive_qa.py:74)、[识图调用](D:/Desktop/AI/chatbot/qq_digest/attachments.py:99)、[两步操作](D:/Desktop/AI/chatbot/qq_digest/web/templates/archive_qa.html:14)。内存模拟捕获待读取 `irrelevant-file`，实际检索结果只有 `matched-text`；没有发真实请求。

### 06 · P1：询问报告会使用报告截止后的消息

步骤：当前报告精确截止 12:00 → 当天 18:00 新消息归档 → 询问这份报告。

证据读取按整自然日取消息，没有使用该版本的 `window_start/window_end`。因此可能把报告之后才出现的事实当作这份报告的依据。合成报告截止 08:02，真实业务函数仍返回 08:03 的消息。

建议：使用当前报告版本的正文及精确时间窗；如要补充后续讨论，单列“截至当前归档”的范围和来源。

代码：[问答范围](D:/Desktop/AI/chatbot/qq_digest/report_qa.py:59)、[已有精确窗口处理](D:/Desktop/AI/chatbot/qq_digest/report_regeneration.py:23)。证据：`browser-additional.json` 的 `report_cutoff_ignored`。

## 其余确认问题

下表的“源码链路”表示当前调用关系能确定该行为；“模拟”表示执行实际模板函数并模拟接口返回；“浏览器”表示在真实浏览器的合成配置下复现。

| 编号／优先级 | 操作与不合理行为 | 建议与验证 | 主要代码 |
| --- | --- | --- | --- |
| 07 · P2 | 群详情点击“查看消息”，进入带群筛选的关键词首页，却不显示任何已有聊天消息。用户仍需猜一个关键词。 | 提供该群聊天浏览入口，或将按钮明确命名为“查找这个群”。浏览器确认有归档消息仍显示搜索空首页。 | [群详情](D:/Desktop/AI/chatbot/qq_digest/web/templates/group_details.html:47)、[搜索空输入分支](D:/Desktop/AI/chatbot/qq_digest/web/templates/find_script.html:26) |
| 08 · P2 | 首次设置允许留空 QQ 账号并导入已有解密库，但常规更新／缺口检查固定刷新 QQ，现有库可读也会失败。 | 依据数据源模式支持“读取已有库”，刷新设为明确选择。源码链路。 | [首次设置](D:/Desktop/AI/chatbot/qq_digest/web/templates/first_use.html:22)、[固定刷新](D:/Desktop/AI/chatbot/qq_digest/web/templates/collect.html:163) |
| 09 · P2 | 群已经有任意归档时，“更新消息”默认仅近七天；离线数周后的更早缺口仍未补回，结果却可显示成功。 | 默认范围结合最后成功水位；如限定七天，行动名称与结果明确表达范围。源码链路，属于需要调整的默认策略。 | [更新范围](D:/Desktop/AI/chatbot/qq_digest/web/templates/group_details.html:44) |
| 10 · P2 | 修改 AI 月度上限／费用单价但不保存，点击“刷新用量”，草稿被旧设置覆盖。 | 统计刷新与策略重载分离，参照存储设置保留 dirty 草稿。浏览器：99 被改回 0。 | [用量渲染](D:/Desktop/AI/chatbot/qq_digest/web/templates/ai_usage_script.html:7)、[加载](D:/Desktop/AI/chatbot/qq_digest/web/templates/ai_usage_script.html:31) |
| 11 · P2 | 首次导入成功后修改日期，不再导入而直接“继续”，向导仍认为已完成，新范围未生效。 | 日期变化作废对应导入结果；继续时核对已完成范围。源码链路。 | [继续条件](D:/Desktop/AI/chatbot/qq_digest/web/templates/first_use_script.html:34)、[预览日期](D:/Desktop/AI/chatbot/qq_digest/first_use.py:344) |
| 12 · P3 | 打开备份设置后，“打开备份目录”一直禁用；必须本页新建一次备份才启用。 | 桌面能力初始化成功后允许打开备份目录。浏览器及源码确认。 | [按钮默认状态](D:/Desktop/AI/chatbot/qq_digest/web/templates/settings.html:62)、[唯一启用位置](D:/Desktop/AI/chatbot/qq_digest/web/templates/settings.html:266) |
| 13 · P2 | 待确认知识点击“编辑后入库”并保存，只修改候选，仍是 pending，不出现在知识库。 | 改为“编辑候选”，或明确实现“保存并入库”。浏览器确认状态仍 pending。 | [操作文案](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:90)、[仅 PATCH](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:112) |
| 14 · P2 | 候选保存请求期间关闭详情，保存成功回调读取已清空的 currentCandidate，提示“保存失败”。 | 提交时捕获 ID；数据保存与视图刷新分开，已关闭不再刷新。模拟复现。 | [保存回调](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:112)、[关闭](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:121) |
| 15 · P2 | 筛选候选后入库／稍后／恢复审核，会整页提交并回默认全群筛选；忙时进入裸错误页。 | 异步提交、保留筛选，错误原地重试。源码链路。 | [HTML 表单](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:48)、[固定重定向](D:/Desktop/AI/chatbot/qq_digest/web/app.py:2012) |
| 16 · P2 | 候选超过 200 条时只显示最近 200 条，数量却像完整总数，没有分页和截断说明。 | 返回 total/page 并分页；至少注明当前上限。源码链路。 | [查询上限](D:/Desktop/AI/chatbot/qq_digest/candidates.py:106)、[数量显示](D:/Desktop/AI/chatbot/qq_digest/web/templates/candidates.html:50) |
| 17 · P2 | 同时填写多个待办建议，处理其中一个后全列表重建，其他未保存草稿消失。 | 按 key 保存草稿，或只更新变更卡片。模拟复现。 | [全列表加载](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:31) |
| 18 · P2 | 快速切换待办状态筛选，较早请求迟到，会显示与筛选框不一致的结果。 | 请求 token＋提交筛选快照，忽略旧响应。源码链路。 | [无请求版本的加载](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:31) |
| 19 · P2 | 保存知识时原消息读取失败，点击“重试读取”会重新初始化窗口，清空已填标题／说明／链接。 | 新建初始化和当前目标读取重试分开。源码链路。 | [初始化及重试](D:/Desktop/AI/chatbot/qq_digest/web/templates/knowledge_save.html:20) |
| 20 · P2 | 刚打开生成面板并填写新范围，迟到的上次任务恢复响应会覆盖新日期和群偏好。 | 恢复任务状态与填表分离，用户编辑优先。模拟复现。 | [恢复条件](D:/Desktop/AI/chatbot/qq_digest/web/templates/summary_progress_script.html:51)、[覆盖字段](D:/Desktop/AI/chatbot/qq_digest/web/templates/summary_progress_script.html:53) |
| 21 · P2 | 发送问题 A 后继续写问题 B，A 返回时无条件清空输入，B 草稿消失。 | 只清空已提交内容，响应不能覆盖新输入。模拟复现。 | [清空输入](D:/Desktop/AI/chatbot/qq_digest/web/templates/reports.html:410) |
| 22 · P2 | “阅读本次摘要”按日期／群打开全部类型，混入已有日报和其他区间报告；失败群也可能显示旧报告。 | 根据本次返回的报告标识打开结果；另设阅读整个范围的入口。源码链路。 | [结果跳转](D:/Desktop/AI/chatbot/qq_digest/web/templates/summary_generation_script.html:80)、[范围设置](D:/Desktop/AI/chatbot/qq_digest/web/templates/summary_reading_script.html:135) |
| 23 · P2 | 选择历史版本失败，只显示短暂 toast，正文一直“正在读取历史版本”。 | 正文显示错误＋重试或保留上一份成功内容。模拟复现。 | [加载及失败处理](D:/Desktop/AI/chatbot/qq_digest/web/templates/reports.html:224) |
| 24 · P2 | 进入话题页必须先更新成功才读取已有列表；同时话题和来源 GET 占变更锁，采集／摘要期间连浏览也返回忙。 | 先读已有内容，刷新独立；区分只读与变更资源。初次更新失败模拟复现，锁链路核对。 | [初次更新](D:/Desktop/AI/chatbot/qq_digest/web/templates/topics_script.html:52)、[所有请求占变更锁](D:/Desktop/AI/chatbot/qq_digest/web/topic_routes.py:73) |
| 25 · P2 | 到期提醒来源跳默认“待确认建议”，而非正式待办；资源跳默认 pending；关键词来源也未定位触发消息。 | 来源带 task_id/candidate_id/msg_id，目标消费参数并定位。源码链路。 | [来源地址](D:/Desktop/AI/chatbot/qq_digest/reminders.py:211)、[待办默认视图](D:/Desktop/AI/chatbot/qq_digest/web/templates/tasks.html:13) |
| 26 · P2 | 未读提醒第 2 页只剩一条，标已读后仍取第 2 页，显示“第 2 / 1 页”和空列表。 | 列表数量缩小时退回最后有效页，参照收藏页。模拟复现。 | [修改后加载](D:/Desktop/AI/chatbot/qq_digest/web/templates/reminders_script.html:18)、[分页渲染](D:/Desktop/AI/chatbot/qq_digest/web/templates/reminders_script.html:20) |
| 27 · P2 | 群已停用但仍勾选附件读取，“读取待处理”可点，后端却提示先开启附件读取，隐藏了真正的群停用原因。 | 返回有效权限和 disabled_reason，按钮状态与后端一致。源码链路。 | [权限返回](D:/Desktop/AI/chatbot/qq_digest/web/attachment_routes.py:38)、[按钮](D:/Desktop/AI/chatbot/qq_digest/web/templates/attachment_controls.html:21) |
| 28 · P2 | 生成／摘要来源／报告问答弹层没有一致的焦点与背景滚动隔离，Tab 可逃出遮罩。 | 复用已有查找弹层的焦点约束、背景锁及关闭后焦点返回。浏览器确认生成面板末尾 Tab 进入 BODY。 | [生成面板打开](D:/Desktop/AI/chatbot/qq_digest/web/templates/summary_generation_script.html:21)、[已有查找处理](D:/Desktop/AI/chatbot/qq_digest/web/templates/find_script.html:41) |
| 29 · P2 | 会话过期直接跳登录，未保存的表单没有统一保护，登录成功一律回摘要，而非原操作页面。 | 安全的站内 return_to＋按需草稿恢复；敏感密钥不保存草稿，原提交不自动重发。源码链路。 | [401 处理](D:/Desktop/AI/chatbot/qq_digest/web/templates/base.html:380)、[登录后跳转](D:/Desktop/AI/chatbot/qq_digest/web/app.py:774) |

## 产品设计建议

这些需要结合使用场景取舍，不列为已确认代码缺陷。

1. **导航明确主入口、内容工具和当前页面操作。** 四个主入口继续保留，二级工具条标明是工作区快捷入口；保留用户要求的“阅读摘要”返回入口。设置有九类，知识和提醒又同时出现在全局工具及类别中，需明确所在位置，避免所有功能都像“摘要的子页面”。
2. **两套问答采用一致的操作体验。** 统一“提问”名称、输入、范围摘要、回答和来源查看；“当前摘要”和“所选归档”仍保持各自证据边界。询问报告的来源也应打开完整原文和连续上下文，现在只有最多 240 字片段。
3. **收起弹层保留任务与草稿。** 关闭报告问答立即清空历史，但后台请求继续；应明确“收起”和“结束会话”的差别，结束不能假装已取消无法取消的后台请求。生成、保存知识和来源面板沿用一致规则。
4. **待确认内容提供完整处理闭环。** 候选已有忽略服务，页面却缺少忽略及恢复入口；已入库知识缺少直接纠错编辑。建议统一编辑／入库／稍后／忽略，关闭审核功能时已有知识仍能维护。
5. **空状态给一个具体行动。** 收藏为空时只显示“暂无收藏”，可提供回摘要的按钮和一句收藏方法。首次设置维护已有安装时，提供明确的暂停原因与退出／恢复自动运行路径；离开向导不等于完成或撤销设置。
6. **共用交互基础模块。** 请求 token、草稿版本、分页越界、提交忙状态、错误重试和弹层生命周期目前各页面重复实现，且完善程度不同。提炼小模块逐页迁移，保留各业务契约；收藏／知识已有分页回退、查找已有焦点约束、存储已有草稿保护，可直接作为本项目基准。

## 网上项目可以借鉴什么

以下只参考官方资料中的具体行为，没有把其外观或功能列表直接套到本项目。

- **Open WebUI：内容集合、处理状态和对话引用分工清楚。** 官方知识库流程是创建集合、上传、等待处理完成，再在对话中附加集合并核对来源。本项目可借鉴为“待读取附件 → 读取状态 → 可用于摘要／问答”，让用户知道何时发生识别及当前证据是否可用；暂时不需要新增向量库来解决这些交互问题。[知识库与文档问答官方说明](https://docs.openwebui.com/features/workspace/knowledge/)
- **Khoj：提问时明确约束资料范围。** 官方对话提供文件筛选以及限定笔记／一般知识的命令，并展示引用资料。本项目可借鉴一个统一提问入口和清楚的范围选择，保留当前报告／所选群与日期的限制；此处是基于其资料范围控制作出的产品建议。[Khoj 对话官方说明](https://docs.khoj.dev/features/chat/)
- **wallabag：保存、编辑、加标签、收藏及归档各自独立。** 官方文章保存流程让已保存项目显示现有标题、标签、收藏与归档状态，分别提供修改动作。本项目可借鉴状态立即可见和动作含义明确，优先修正“编辑后入库”以及提醒来源无法定位的问题。[wallabag 保存文章官方说明](https://doc.wallabag.org/user/articles/save/)

官方资料通过 AnySearch 搜索和提取，原内容保存在 QA 目录。参考资料支持上述借鉴点；本项目缺陷结论来自本地代码及复现。

## 建议实施顺序

1. **先修已保存内容、恢复与范围错误**：01、02、03、04、05、06。为每项补上对应回归：开启设置后刷新保存、恢复 A/B 响应乱序、多窗口待办、待切换写入、预览不新发附件、精确报告截止。
2. **再统一草稿与异步状态**：10、14、17、18、19、20、21、23、26、28、29。先抽出请求世代与弹层生命周期，逐页接入，验证迟到响应和关闭／重开。
3. **补操作闭环与定位**：07、08、09、11、12、13、15、16、22、24、25、27。来源直达详情，保存与入库语义一致，已有数据不依赖更新成功才能阅读。
4. **最后收敛导航和文字**：只围绕具体任务显示必要操作，空状态给下一步，边界说明按需展开。当前无需通过增加功能数量解决体验问题。

本轮交付为审查清单和复现证据。程序、桌面快捷方式、真实配置及用户数据均未改动；工作区原有两个未跟踪旧模板保留。
