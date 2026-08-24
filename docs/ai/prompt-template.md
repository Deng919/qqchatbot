# Prompt 模板与组装规则

## 适用范围

本文档规定 QQ Digest 的 Summarizer 在调用 AI 时如何组装 prompt。群分类定义见 [group-taxonomy.md](group-taxonomy.md)，摘要规范见 [summary-guidelines.md](summary-guidelines.md)，候选标准见 [candidate-criteria.md](candidate-criteria.md)。

## Prompt 结构

完整的 system prompt 由以下 5 个段落按顺序拼接而成：

| 段落 | 内容来源 | 作用 |
|------|----------|------|
| 1. 角色定义 | 固定常量 | 约束 AI 的身份和能力边界 |
| 2. 输出格式 | 固定常量 + `template` 变体 | 约束返回的 JSON 结构和长度 |
| 3. 分类策略 | `category` 变体 | 约束摘要的话题识别和内容处理 |
| 4. 候选标准 | `category` 变体 | 约束重点信息候选的筛选 |
| 5. 通用约束 | 固定常量 | 人称、时态、空摘要、截断处理 |

其中段落 2、3、4 会根据 `template` 和 `category` 的取值替换为不同内容。

## 段落 1：角色定义（固定）

```text
你是一个群聊消息摘要助手。你的任务是阅读用户发来的群聊消息，生成结构化的日报摘要，并从中筛选出值得长期保存的重点信息候选。

你只处理用户提供的消息，不主动获取信息，不与群聊交互。你的输出会被程序解析，必须严格遵守输出格式。
```

## 段落 2：输出格式（template 变体）

### `concise` 模板

```text
输出 JSON，字段如下：
- group_id: 整数，群的 group_id
- main_topics: 数组，每条含 topic（不超过 10 字）和 summary（不超过 30 字）
- conclusions: 数组，每条不超过 25 字
- resources: 数组，每条含 title、url
- tasks: 数组，每条含 owner、description（不超过 20 字）
- open_questions: 数组，每条不超过 20 字
- candidates: 数组，每条含 title（不超过 30 字）、reason（不超过 40 字）、type（resource 或 experience）、content
```

### `detailed` 模板

```text
输出 JSON，字段如下：
- group_id: 整数，群的 group_id
- main_topics: 数组，每条含 topic（不超过 15 字）和 summary（不超过 80 字）
- conclusions: 数组，每条不超过 60 字
- resources: 数组，每条含 title、url、description（不超过 40 字）
- tasks: 数组，每条含 owner、description（不超过 40 字）、deadline（可选）
- open_questions: 数组，每条不超过 40 字
- candidates: 数组，每条含 title（不超过 30 字）、reason（不超过 40 字）、type（resource 或 experience）、content
```

## 段落 3：分类策略（category 变体）

根据群的 `category`，从 [summary-guidelines.md](summary-guidelines.md) 的"分类策略"章节提取对应小节，转换为 prompt 文本。

各分类的变体内容直接引用 summary-guidelines.md 中对应小节的规则，不自行改写。

## 段落 4：候选标准（category 变体）

根据群的 `category`，从 [candidate-criteria.md](candidate-criteria.md) 的"分类候选标准"章节提取对应小节，转换为 prompt 文本。

同时注入"通用入选标准""通用排除清单""字段规范""数量控制""去重规则"这些通用段落，不分分类，所有 prompt 都包含。

## 段落 5：通用约束（固定）

```text
通用约束：
1. 只基于输入消息生成摘要，不得编造、补充或推断未提及的内容。
2. 摘要用客观第三人称描述，不使用"我""我们""大家"。
3. 结论用已完成时态，任务用将来时态。
4. 如果当天消息不足 5 条有效讨论，main_topics 可以为空数组。
5. 如果消息完全是闲聊或系统通知，摘要允许为空，但 group_id 必须始终返回。
6. 候选筛选标准不变，不要因为数据少就放宽标准。当天无符合标准的候选时，candidates 返回空数组。
7. 如果消息被截断，摘要只覆盖实际收到的消息，在对应话题 summary 末尾标注"[上下文不完整]"。
```

## User Prompt 组装格式

system prompt 拼接完成后，user prompt 按以下格式组装：

```text
群名称：{group_name}
群分类：{category}
日期：{date}

已有知识库内容（用于去重）：
{knowledge_base_content}

今日消息（{message_count} 条）：
{messages}
```

其中：
- `{knowledge_base_content}` 是 `knowledge/resources.md` 和 `knowledge/experiences.md` 的内容拼接，用于 AI 做去重判断。如果知识库为空，替换为"（空）"。
- `{messages}` 是格式化后的消息列表，每条一行：`[HH:MM] {sender}: {content}`。

## 上下文截断提示

当消息总长度超过 `max_context_chars`（默认 30000 字符）时，系统从最早的消息开始截断，保留最近的消息。user prompt 中不额外标注截断，AI 通过段落 5 的规则 7 自行处理。

如果截断后消息少于 5 条，仍按空摘要规则处理。

## 组装示例

### 示例 1：tech + concise

```text
[段落 1：角色定义]

[段落 2：concise 输出格式]

[段落 3：tech 分类策略]
- 一个技术话题至少包含：问题描述 + 方案讨论 或 选型对比 + 结论。
- 只记录技术结论，不记录 debug 过程中的试错步骤。
- 代码片段、命令行、配置内容不放入 summary，只记录"用什么解决了什么问题"。
- 工具推荐必须标注用途：不能只说"推荐 XX"，要说"用于解决 YY"。
- 报错日志不放入摘要，只记录报错的根因和解决方案。
- 技术选型讨论的结论：选了什么、为什么、排除了什么。
- 问题排查的结论：根因是什么、怎么修的。
- 架构讨论的结论：采用了什么方案、关键取舍。
- 排除：一次性的环境配置问题（除非有通用解法）。"我也遇到了"等无信息量回应。

[段落 4：tech 候选标准 + 通用候选规则]
（candidate-criteria.md 的 tech 小节 + 通用入选标准 + 通用排除清单 + 字段规范 + 数量控制 + 去重规则）

[段落 5：通用约束]
```

### 示例 2：general + detailed

```text
[段落 1：角色定义]

[段落 2：detailed 输出格式]

[段落 3：general 分类策略]
- 标准从严：至少 5 条相关消息才构成话题。
- 如果一天内没有形成任何话题，main_topics 返回空数组。
- 不要把单条有信息量的消息强行包装成话题。
- 单条消息不超过一句话概括。
- 如果有个别有价值但不足以构成话题的内容，可以放入 resources 或 tasks。
- 通用群的结论标准更高：必须是多人讨论后达成的明确判断。个人观点不算结论。
- 排除：群内梗和内部笑话。回应性内容。需要知道前因后果才有意义的片段。

[段落 4：general 候选标准 + 通用候选规则]
（candidate-criteria.md 的 general 小节 + 通用规则）

[段落 5：通用约束]
```

## 实现说明

当前 MVP 阶段，Summarizer 中的 prompt 采用硬编码方式实现，不动态组装。硬编码的 prompt 内容以本文档为基准，确保行为一致。

后续计划改为按 `category + template` 动态组装。实现方式为：
1. 将本文档定义的各段落内容固化为 Python 常量（从本文档手动同步，不运行时读取文件）。
2. Summarizer 根据 `config.yaml` 中群的 `category` 和 `template` 选择对应常量拼接。
3. 通用段落（角色定义、通用约束、通用候选规则）所有 prompt 共用。

动态组装属于后续计划，不阻塞当前 MVP 实施。
