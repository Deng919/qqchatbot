# 摘要卡片阅读实施计划

> 使用 subagent-driven-development 执行独立后端任务，主代理完成页面整合，完成后按需求与代码质量复审。

**目标：** 摘要页默认直接阅读日报要点，提供按群预览，将原表格保留在管理报告中；不改变生成模型、报告格式和数据。

**结构：** 复用 CatchupService 的逐条来源和稳定已读键，阅读接入拆到 summary_reading_routes.py。报告预览从现有 JSON 提取概览和至多三条主要话题。页面交互与样式分别放入模板局部文件，原报告详情、问答、纠错与生成逻辑保留。

**技术：** FastAPI、SQLite、Jinja、原生 JavaScript/CSS；无新依赖。

## 接口约定

- GET `/api/summary-reading`：`date_from/date_to` ISO 自然日，默认今天；`group_id` 可选；`read_filter=all|unread|new` 默认 all；`since` 可选带时区时间；`page` 默认 1。返回与补看相同的 items（稳定 key、group_name、report_date、section、text、source_ids、status、report_id、report_url、read），以及 total/unread/new_count/page/page_size/skipped_reports。先按时间、群筛选，再按已读/新增筛选，最后分页；new 按报告更新时间判断，不改变未读定义。无 since 时 new 使用全部选定日期内容，不凭空认定已读。
- POST `/api/summary-reading/visit`：复用 visit 返回 previous_viewed_at/viewed_at，只更新上次打开时间，不改已读。
- POST `/api/summary-reading/read`：`{key,read}`，复用稳定键校验与已读保存；需登录。三个接口为核心阅读功能，不受旧 catchup 开关限制。
- GET `/api/reports` 现有字段保持，增加 `preview`（overview、points 至多三条、available）；坏文件返回 available=false，不造内容、不阻止其他报告显示。

## 任务 1：阅读服务及接口

- [x] 增加跨群时间/群/未读/新增筛选回归测试，覆盖旧未读不被 visit 丢弃、筛选先于分页、时区、坏文件、无鉴权拒绝以及 catchup=false 时核心阅读可用。
- [x] 运行失败测试后扩展 CatchupService，保持旧接口兼容与 key 算法不变。
- [x] 创建 summary_reading_routes.py，app.py 仅挂载；创建 report_previews.py，列表增加预览。
- [x] 用默认 Python 运行 tests/test_catchup.py 及新增接入测试。

## 任务 2：摘要阅读页面

- [x] reports.html 增加“要点阅读／按群阅读”，更多菜单提供“管理报告”；原表格保留但默认隐藏。
- [x] 顶部统一群和日期筛选；初始今天，最近七天包含今天；新要点筛选区分全部/未读/上次打开后新增。报告类型只用于按群及管理视图。
- [x] 要点卡显示群、日期、类型、原文首句标题和正文；长文展开；多个来源先通过单一入口展开来源列表，再调用已有同群同日期来源校验接口。
- [x] 按群卡使用 overview 与三条话题预览，日报/范围显式标注；缺失文件说明原因。
- [x] 详情采用独立阅读区域，返回恢复筛选、列表及滚动；通过 URL/sessionStorage 保存视图、筛选、分页、展开键和滚动。日常阅读不把版本与纠错表单铺在正文前。
- [x] 日报结果持久显示“阅读新摘要”，范围结果提供对应日期/群范围的按群入口；没有摘要时提供今天生成和最近七天入口。
- [x] 移除一级补看导航与首页重复入口；设置目录不再展示旧补看开关，数据库旧值与接口兼容保留。

## 任务 3：验证与发布

- [x] 测试核心阅读在精简功能设置下可用、来源/已读有效、旧报告详情及范围生成保持兼容。
- [x] 使用合成报告运行浏览器，验证 1280/390 宽度、三视图、筛选、来源、展开、已读、新增、返回恢复、空状态；检查控制台。
- [x] 复审修复重要问题，更新 ROADMAP；后台隐藏打包到 D:\Apps\QQDigestDesktop-2026-10-02-Reading，核对包内模块/模板并更新桌面快捷入口，保留偏好与启用的开机自启。
- [x] 明确暂不宣称实际 EXE 启动通过；原自动审批审核拦截仍未解除。提交并推送仓库。

## 完成与验证记录

- 2026-10-02：需求复审、最终质量复审通过。全量回归 **542 passed**，有一条既有 Starlette/httpx 弃用提示；版本切换测试同步加载新拆出的真实正文渲染模块。
- 修复复审发现的重新进入新增范围不更新、昨日/跨午夜生成后的跳转日期，以及零生成仍出现阅读入口；新增后端日期回归覆盖 today/previous_day、配置时区、success/partial_success。
- 合成数据浏览器验证：精简功能设置下阅读可用，三视图、今天/七天/自定义日期、旧未读与新增独立、刷新、长文及群预览展开、原消息、Escape 关闭、详情返回状态、空日期、日报及范围结果入口、零生成和全失败提示均通过。1280×900、390×844 无横向溢出，控制台无警告或错误。
- 测试材料保存到 `D:\Cache\QQDigestDesktop\summary-reading-qa-2026-10-02`（含 desktop.png、mobile.png、final-suite.txt）；模拟生成不调用真实 AI，不读取真实聊天消息。临时服务器及浏览器标签已关闭，视口已恢复。
- 桌面发布：`D:\Apps\QQDigestDesktop-2026-10-02-Reading\QQDigestDesktop.exe`；核对 7 个模板哈希、2 个新 Python 模块、版本标识及桥接隐藏窗口标志。桌面入口 `D:\Desktop\QQ Digest.lnk` 已指向该版；共享配置保留，开机自启保持关闭。
- 实际 EXE 启动仍因此前自动审批审核拦截未验证，本次完成源码浏览器交互与打包内容核对。全局导航重组与其他扩展功能改造继续按后续计划推进。
