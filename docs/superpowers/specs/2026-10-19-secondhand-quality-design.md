# 二手信息质量与行为标记设计

本次修正针对已导入的二手群数据：同一条消息中的整包交易不能被拆成互不相关的商品；同一发布人的重复出售、长期求购和疑似推广需要可解释地标记；每个说明、成色和价格显示都必须能回到同一条原文证据。

## 目标

- 明确打包、全套、整包、合集、一起出售等表达时，保留一个可展开的打包条目；只有各项有独立价格或明确单独出售时才拆分。
- 对同一卖家和标准化标题的来源快照计算重复次数、涉及群数、活跃天数和时间跨度，显示“重复发布”“长期求购”等行为标记。
- 对多商品服务清单、会员/权益/课程/代开等模式显示“疑似推广”，不把推断写成事实。
- 说明、成色、包名和价格片段只能使用同一 source.text 中的连续原文；不能用 AI 补写的同义句替代证据。价格展示使用可核对的短片段。
- 保留现有原始消息、转发路径、图片状态和源证据；整理过程幂等，失败不会覆盖已有有效条目。

## 非目标

- 不自动删除原始消息、归档或现有群设置。
- 不把仅凭相似图片或昵称的条目合并。
- 不把“疑似推广”当成违规判定，也不隐藏其原文。
- 不接入 QQ 内部 API 或要求用户登录第三方客户端。

## 数据与接口

`market_posts` 新增 `bundle_key`、`bundle_label`、`bundle_mode`、`components_json` 四个字段。`bundle_mode` 为 `single` 或 `bundle`；同一来源中的打包组件共享 `bundle_key` 与 `item_key`，组件名称和证据保存在 JSON 中，列表只显示一个打包条目，详情可展开组件与全部来源。

`SecondhandService.list_items` 在每个条目上返回 `repeat_count`、`active_days`、`span_days`、`behavior_flags`、`bundle_mode`、`bundle_count` 和 `components`。重复发布条件为出售方向且来源数至少 3，并且活跃天数至少 2 或涉及群数至少 2；长期求购条件为求购方向且本地日期跨度至少 3 天；疑似推广使用多组件清单或会员、权益、年卡、课程、资料、代开、代购、优惠、快递等来源词，标签必须使用“疑似推广”。

## 原文校验

`description`、`condition`、`bundle_label` 和 `price_text` 必须是 evidence 的连续子串；不满足时拒绝该来源或清空可选字段。`price_text` 长度限制为短片段，不能把完整商品标题作为价格。已有数据重新从原始来源生成，不修改 raw messages。

## 页面

列表卡片在标题旁展示打包和行为标记，并显示“来源数 / 群数 / 活跃天数”。详情页展示打包组件和行为统计，所有来源仍可打开原消息与图片。现有筛选、收藏、屏蔽和默认最近七天保持不变。

## 验收

- 新增行为和原文约束测试先失败，再通过；旧测试全量通过。
- 对现有二手群最近七天在副本上重跑，打包条目不再按组件重复占位，行为标签可解释，原始证据可逐条打开。
- 完成全量 pytest、CJS UI 测试和独立浏览器检查后再替换展示数据、打包并提交。

## 实现决定（本次锁定）

- 打包是单行父条目模型：父行保留 `title`、`direction`、`status` 和一个可核对的整体 `price_text`；`components_json` 是解析后的列表，每项至少含 `title` 与 `evidence`，可带 `price_text`，API 返回解析后的列表而不是 JSON 字符串。`bundle_count` 是父行组件数，`source_count` 仍是跨群来源数。
- `bundle_key` 为 source-scoped 的稳定摘要；跨来源同一商品仍按既有 `item_key` 合并，组件只取当前最新来源的列表。独立价格或明确单独出售的 payload 不使用 bundle 模式。
- `bundle_mode` 默认 `single`，`bundle_key`/`bundle_label` 默认空串，`components_json` 默认 `[]`。空 description、condition 和 bundle_label 合法；非空值必须是 evidence 的连续子串。title 仍要求至少一个 token 可在 evidence 中核对。
- `price_text` 允许空串（代表未知价格），非空时必须是最多 24 个字符的数字短片段：可选货币符号、数字/小数和元/块/rmb/r 单位；`price` 非空时必须能从该片段读出同值。`price is null` 时非空 price_text 仍可保留作为未解析价格证据。
- 活动指标使用所有未屏蔽且所属启用群的 source rows；`active_days` 为本地时区去重日期数，`span_days` 为首尾日期含端点天数。无 seller QQ 或 original time unknown 的行不跨源合并，且不参与行为标签。日期/群筛选只决定列表是否显示，不缩短统计范围。
- `repeat_count` 等于 source_count。重复发布为 sale 且 source_count>=3 且 active_days>=2 或 group_count>=2；长期求购为 want 且 span_days>=3。疑似推广为 bundle_count>=3 或任一来源的标题/证据/组件命中推广词；标签只影响展示。
- 保留现有 `possible_duplicate`、`group_count`、`source_count`、最新价格回填和 detail 顶层结构。重建通过显式 force rebuild 清空 done scan receipts 的派生条目后重新识别；空 items 才删除该 source 的旧 rows，AI 异常保留旧 rows。


## 真实数据核验后的修订

- `collection` 表示同帖未单独定价的多物品清单，不宣称卖家要求整包购买；明确打包的才是 `bundle`。同来源局部组件引文在原文有打包依据时也能还原整体，独立价格或“单独/分别”表达优先。父条目为单行，组件以 JSON 保留。
- `repeat_count` 按可信卖家 QQ + 方向 + normalized(title) 计算相关来源数，实体商品仍保守区分；历史查询不使用结束日期以后的活动。`source_count` 仍是该实体商品的来源数，两者可不同。
- 疑似推广需服务词加清单/权益/推广证据。普通多件桌游和附课程资料的个人转让不因词汇或组件数量直接判推广；标签不改变方向、成交状态或原始证据。
- 全量 AI 重跑副本只有 416/596 条来源处理成功，180 条失败，故不发布该副本。最终采用 `secondhand_repair.repair_existing` 对现有 252 条已提取来源逐条重新校验，清空无法核对的可选说明并规范已知价格；不重猜缺失商品或价格。保留 11 条原有识别失败和 7 条转发未解析状态，不能声称它们已恢复。
- 修复只在隔离副本执行；消息/扫描表逐值签名保持一致，修复重复执行后派生行须完全相同。发布前在协调锁下核对生产 market_posts、market_favorites 未变化，再原子替换这两张派生表。
- 收藏按实际商品/组件标题映射，不能把来源中其他独立商品一并收藏。自动还原与 AI 显式打包使用相同实体 key，重复提取不丢收藏。
