# 连续阅读：有限续写、关键介入与恢复

连续阅读已提供 CLI 和 HTTP 入口。它沿现有 StoryBundle 中明确标记的场景前进，一次请求最多发布 1–8 个新场景，默认 3 个。真正的分歧等待玩家决定。

当前是基于场景图的第一版；自动拆书的结果仍主要是情节弧，尚未实现从任意小说自动规划高质量改编场景。配套《最后一班渡船》为原创素材与人工设计的场景图，用于检验连续阅读的行为。

## 离线体验

在项目根目录，使用已安装的 `trpg` 命令：

```bash
trpg story-new examples/story/last_ferry.yaml --session-id ferry --player-name 林岑
trpg story-read examples/story/last_ferry.yaml ferry --request-id opening
```

开头连续经过“第一次铃响”和“一分钟”，然后显示 `stay` / `cross`。Fake 写手默认返回场景素材，用于验证流程，不代表模型文学质量。

选择留下帮忙，并继续阅读：

```bash
trpg story-read examples/story/last_ferry.yaml ferry --choice stay --request-id stay-path
```

一次决定之后会连续发布“门后的木板”“铃声停了”“一个确切的回音”。故事记住信件仍未送达，不会在结尾无故变成两件事都完成。

要比较另一条路线，在作决定之前创建分支：

```bash
trpg story-branch ferry crossing
trpg story-read examples/story/last_ferry.yaml ferry --branch-id crossing --choice cross --request-id cross-path
```

真实写手使用 `--author llm`，将当前场景与允许的会话上下文发送到项目配置的 LLM endpoint。运行前确认该 endpoint 是你想使用的服务。CLI 的默认 Fake 模式不调用网络。

## 预算与恢复

- `--scenes N` 是整个阅读请求最多发布的场景数，范围 1–8；关键决定和故事结尾可以提前结束请求。
- `--request-id ID` 标识一个不可变请求。失败或断线后，使用相同 ID、场景预算、choice 和 branch 重试；已发布的场景直接复用，不再次调用写手。
- 一个已完成请求重复执行会返回原报告，即使故事后来继续了。要接着读、改变预算或作新选择，使用新的 request-id。
- 未指定 ID 时 CLI 会生成并在开始前显示 ID。进程中断后可用该 ID 恢复。
- 取消/失败停止当前生成；之前完成的场景保留。恢复需要重新发起请求，当前没有后台自动重试或调度服务。
- 同一分支若在未完成的请求外又产生了新回合，旧请求恢复会返回冲突，避免错误合并两种走向。已发布内容仍在会话历史中。
- 同时生成的请求仍可能各产生一次模型调用，但基于同一版本的结果只有一个能提交；客户端收到冲突后应读取存档并决定是否重试。

预算限制发布场景数，不保证 token 或费用上限；模型的 token/timeout 配置仍独立生效。本层不自动循环重试失败的 provider 调用。

带 `boundary` 的场景在真实写手生成后增加一次语义审查调用；每场景尝试最多为一次写作加一次审查（底层 transport 重试另计）。未通过的草稿不发布，之前的正文保留。接口以 HTTP 502 / `scene_review_rejected` 告知草稿被拒绝，可用同一请求恢复；Fake 模式不执行模型审查。

## HTTP

```http
POST /api/story/sessions/ferry/read?branch_id=main
Content-Type: application/json

{"request_id":"opening","max_scenes":3,"fake":true}
```

响应包含 `scenes`（本请求的全部已发布正文）、`current_beat_id`、`turn_number`、`version`、`choices` 和 `stop_reason`：

| stop_reason | 行为 |
| --- | --- |
| `awaiting_choice` | 停在需要玩家决定的场景，返回可选方向 |
| `budget_exhausted` | 本次发布额度用完，可新建请求继续 |
| `completed` | 故事完成 |

玩家介入通过新请求的 `choice_id` 提供；该选择生成的场景也计入预算。预算用完的同一时刻若到达决定或结尾，优先报告决定/结尾。

请求归属、参数或存档版本冲突为 HTTP 409；非法预算为 422。HTTP 默认 `fake: true`。正文响应不包含 provider debug。当前 endpoint 在请求中等待有限生成，尚无实时进度流或专用阅读页。

## 素材与存档契约

`StoryBeat.decision_required` 默认 `true`。只有显式设为 `false`、且恰有一个后继的非终止场景才允许自动推进；不根据“只有一个选项”或选项标题擅自推断玩家同意。导入器及编译器新输出的机械 continue 边会显式标记为自动。

可选 `boundary` 描述 `entry_facts`、非空的 `exit_facts` 及 `forbidden_events`，限定写手的场景范围。它是创作与审查的约束，不是把未来计划当作已经发生的存档事实。当前原创样本已提供这些边界；自动编译器尚未自动提取它们。

新会话保存 Bundle 内容摘要，包含剧情、效果与介入策略。素材改变后，继续生成被拒绝；恢复原故事包或创建新会话。格式化 YAML 或字段排序变化不会改变摘要。

旧存档缺少摘要时仍可使用原有 `story-play`，但连续阅读要求创建绑定素材的新会话；系统不会把当前磁盘文件静默认作旧存档原素材。摘要用于检测变更，当前不自动保存或找回历史故事包文件。

每个阅读请求保存起始版本和预算，每个场景使用请求内部的稳定回合 ID。正文、快照和回合结果同事务提交，因此在“正文提交之后、请求报告保存之前”中断，也能从已提交场景恢复。玩家的重要选择另存为 `decisions`；自动 continue 不会覆盖或冒充这些决定。

## 验证范围

回归覆盖 CLI/HTTP、预算与重放、provider 失败、任务取消、保存最终报告前中断、外部回合竞争、素材变化、分支后果和真实 provider adapter 的 mock transport。固定文学样本与真实模型试读用于后续质量评估；离线流程通过不等于文学质量已经合格。

第一轮真实试读发现正文提前替玩家作决定，并存在重复和时空冲突，**叙事质量未通过**。问题与改进记录见 [试读报告](reading-evaluation-2026-09-17.md)。
