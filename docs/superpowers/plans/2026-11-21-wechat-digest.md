# 微信群整理接入 Implementation Plan

Goal: 在现有 QQ Digest 界面查看并整理本地微信群消息。

Architecture: 负数内部群键配合持久平台来源绑定；组合采集器只读相邻归档，复用现有归档、同步、AI摘要与候选管线。

Spec: docs/superpowers/specs/2026-11-21-wechat-digest-design.md

## 约束与验证重点

- Python固定D:\CodexTools\python\Scripts\python.exe；临时文件在D:\Cache\QQDigest-WeChat-20261121。
- 来源不含伪造QQ号；绑定变更拒绝；所有源查询只读且按指定群过滤。
- 群删除、并发读取、时间边界、分片重号、卡片解析和来源追踪需验证。
- 保留既有未跟踪文件；凭据/配置/聊天内容不入Git；不开启MyCortex自动保存。

## 实施

- [x] 测试先复现微信采集/来源绑定缺失，再新增collector/wechat.py、source配置和来源绑定表。
- [x] 扩展组合采集、周期同步、手动采集/历史检查，现有QQ来源保持原行为。
- [x] 现有群列表显示来源，消息/搜索/摘要/收藏可使用微信群；补回归测试。
- [x] 真实配置与数据备份后接入，核对导入去重，生成真实范围摘要；独立浏览器验收。
- [x] 更新文档与两项目记忆，构建新桌面版本，提交推送并核对远端。

最终记录：2026-11-22，见 docs/verification-wechat-2026-11-22.md。较大范围真实摘要遇到Cockpit502，较小日期成功并校验引文。
