# 微信群整理

更新：2026-11-22。

## 使用入口

在现有“群聊”页选择带“微信”标记的群，通过“查看消息”“查看摘要”“更新消息”使用同一套整理界面。查找、原消息上下文、摘要生成、资料候选审核、收藏和手动 MyCortex 保存共用原流程。MyCortex 自动保存默认关闭，只有用户明确打开后才按其既有规则运行。

本机新版桌面程序为 `D:\Apps\QQDigestDesktop-2026-11-22-WeChat-Release\QQDigestDesktop.exe`，桌面 `D:\Desktop\QQ Digest.lnk` 指向该程序。配置和归档仍在原项目本机目录。

## 数据与调度

相邻中转项目独立采集微信本地记录，每60秒写入其 `data/wechat-group-archive/archive.sqlite`。QQ Digest 仅以 SQLite `mode=ro`、`query_only` 和同一读事务读取这个归档，不访问微信进程或密钥，不发送消息。

Digest 使用原 `collection.interval_minutes`（默认10分钟）导入；原程序关闭时不会执行这一调度。微信导入每轮重读该群设置的 `collection_window_days` 范围并以消息身份去重，支持上游恢复后补写范围内的旧消息；超过此范围的历史需要在“更新消息”中指定日期。日报和手动摘要复用原 Cockpit 设置，所选消息会交给配置的模型整理。附件正文与图片理解仍未接入，链接卡片仅提取文字和 HTTP(S) 地址。

## 配置与来源隔离

源码配置示例（全部为合成值）：

```yaml
wechat_sources:
  - source_key: wechat-local-example
    group_id: -1
    native_group_id: synthetic@chatroom
    name: 微信示例群
    archive_path: D:/ExistingProject/data/wechat-group-archive/archive.sqlite
```

`group_id` 是应用内部负数关系键，不是 QQ 群号。`group_sources` 独立保存微信平台、来源键、原生字符串群标识及账号绑定指纹；配置重复键被拒绝，换账号或群不能沿用旧绑定。确需接另一来源时同时使用新的来源键和内部群ID。删除群会保留来源绑定，不因程序重启自动复活；可通过发现群后重新添加来恢复。

`sender_qq` 留空，原生微信成员标识保留在 `content_json.wechat`；当前未解析真实昵称，界面显示已归档成员标识。消息身份包含账号绑定、群、分片和本地消息ID。没有把微信成员当作 QQ 用户合并。

不可读、被换号或冲突的来源按群失败，已归档内容仍可浏览。QQ刷新失败时微信继续采集/整理；故障群不会改用演示消息。摘要引文仍按群和消息ID核对；MyCortex 使用微信来源标签与应用内 `qqdigest://` 回溯地址。

## 验证与边界

本轮验证见 [验证记录](verification-wechat-2026-11-22.md)。相邻采集器仍只包含电脑已同步且进入其归档的消息，手机独有历史、采集器游标前的迟到插入或编辑不会因本接入自动补齐。小号私聊开关与本功能独立，不控制群采集。
