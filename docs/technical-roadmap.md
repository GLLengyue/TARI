# TARI 技术路线图 | Technical Roadmap

> **中文摘要：** TARI 将先证明“可审计的多 Agent 核心 loop”比单模型 RP 更稳定，再逐步扩展到多角色、分支时间线、成熟客户端和云端/本地混合模型。<br>
> **English summary:** TARI will first prove that an auditable multi-agent loop is more reliable than a single-model RP baseline, then expand to multiple actors, branching timelines, mature clients, and hybrid cloud/local models.

| 项目 | 当前规划 |
| --- | --- |
| 当前版本 / Current version | `v0.1` MVP |
| 首要目标 / Immediate goal | 验证可靠性和可测试性，而不是快速增加功能 / validate reliability and testability before adding features |
| 目标形态 / Target | 可稳定完成多场景短篇战役的 TRPG runtime / a runtime that can complete short multi-scene campaigns reliably |
| 规划方式 / Planning model | 以退出条件推进，不承诺固定日期 / exit-gated phases rather than calendar promises |

## 1. 项目目标 | Project objective

TARI 的核心问题不是“让模型写出更漂亮的 prose”，而是让模型协作过程具备可验证的权限、状态和随机性边界：

TARI is not primarily about generating prettier prose. It is about giving model collaboration verifiable boundaries for authority, state, and randomness:

- 玩家保留对玩家角色意图的决定权 / the player retains control over player-character intent；
- GM 负责提出裁定，规则运行时负责验证并提交 / the GM proposes adjudication while the runtime validates and commits it；
- Actor 只能读取自己的视图，不能把未知信息写成事实 / actors only read their own views and cannot turn unknown information into facts；
- 骰点真正改变后果，而不只是改变措辞 / dice change consequences, not merely wording；
- 每个回合可以审计、恢复、重放，并能定位失败步骤 / each turn can be audited, recovered, replayed, and attributed to a failing step。

## 2. 技术原则 | Technical principles

| 原则 | 约束 |
| --- | --- |
| 运行时权威 / Runtime authority | LLM 只返回 typed proposal；骰点、权限、Spotlight 和状态提交由确定性代码掌握。<br>LLMs return typed proposals only; deterministic code owns dice, permissions, spotlight, and commits. |
| 事件历史不可静默改写 / Immutable history | 历史事件只追加，不被客户端 transcript 或模型输出静默覆盖。<br>Events are appended, never silently rewritten by a transcript or model output. |
| 知识投影隔离 / Knowledge projection | 世界真相、角色信念和玩家已知信息分开建模。<br>World truth, character beliefs, and player knowledge remain separate. |
| 可恢复优先 / Recovery first | 失败回合必须能中止并从最后一个已提交回合恢复。<br>Failed turns must abort cleanly and resume from the last committed turn. |
| Provider 解耦 / Provider decoupling | 模型供应商可替换，不侵入规则、Agent 协议和持久化层。<br>Providers remain replaceable without leaking into rules, agent protocols, or persistence. |

## 3. Phase 0：MVP 验证与问题收集 | MVP validation

**目标 / Goal：** 暂停新增功能，验证当前架构假设和“多 Agent loop 是否值得额外复杂度”。

Pause feature expansion and validate the architecture and whether the multi-agent loop earns its complexity.

### 3.1 三类测试 | Three validation tracks

#### A. 离线 Fake Agent 测试 | Offline Fake Agent tests

验证确定性 runtime：

Validate the deterministic runtime:

```bash
pytest
trpg new examples/station_zero.yaml --fake
trpg play station-zero --fake --debug
```

至少观察以下行为：

- `2d6` 结果和结果档位是否正确；
- Spotlight 是否每轮回到玩家；
- State Patch 是否只由 GM 提交；
- Actor View 是否阻止 GM 隐藏事实泄漏；
- 退出后重新启动能否恢复；
- Replay 是否完整展示骰点和公开事件。

#### B. 真实云端模型测试 | Real provider tests

为 GM、Actor、Auditor 分别配置模型，运行 **20–30 轮**真实游戏。记录：

Configure models independently for the GM, Actor, and Auditor, then run **20–30 turns**. Record:

| 指标 / Metric | 关注点 / What to observe |
| --- | --- |
| GM 错误请求检定 / Invalid GM checks | 不必要或协议错误的检定次数 |
| GM 替玩家行动 / GM taking player agency | GM 是否替玩家决定行动 |
| Actor 越权 / Actor overreach | 未经裁定宣布结果、使用越权知识 |
| Auditor 质量 / Auditor quality | 误报、漏报和 Schema 重试 |
| 运行成本 / Runtime cost | 每轮延迟、token 和估算成本 |
| 剧情流动性 / Narrative flow | 剧情停滞次数 |

#### C. 单模型基线 | Single-model baseline

用同一个场景让单一模型同时承担 GM 和全部 NPC，与 runtime 版本对照。重点比较：

Run the same scenario with one model acting as GM and all NPCs, then compare it with the runtime version. Compare:

- 世界事实一致性 / world-fact consistency；
- NPC 知识隔离 / NPC knowledge isolation；
- 玩家决策权 / player agency；
- 随机结果对剧情的实际影响 / whether randomness changes outcomes；
- 剧情偏离预设方向的能力 / ability to diverge from the planned path；
- 错误是否可以定位到具体步骤 / whether failures are attributable to a concrete step。

### 3.2 Phase 0 退出条件 | Exit gate

只有同时满足以下条件，才进入 Phase 1：

Proceed only when all of the following are true:

- 20 轮内没有不可恢复的状态损坏 / no unrecoverable state corruption within 20 turns；
- 骰点确实改变后果，而不只是改变文案 / dice change consequences rather than wording；
- Actor View 能有效限制秘密泄漏 / Actor View measurably limits secret leakage；
- Spotlight 带来的收益大于额外复杂度 / spotlight benefits outweigh added complexity；
- 多 Agent 流程至少在一项关键指标上优于单模型基线 / the multi-agent flow beats the single-model baseline on at least one key metric。

## 4. 分阶段路线 | Phased roadmap

### Phase 1：加固 MVP 可靠性 | Harden MVP reliability (`v0.2`)

**目标 / Goal：** 将原型提升为可信、可恢复、可测试的运行时。

Turn the prototype into a trustworthy, recoverable, and testable runtime.

#### 4.1 固化两阶段 GM 协议 | Formalize the two-stage GM protocol

明确禁止模型在看到骰点后修改检定条件：

Prevent the model from changing the check after seeing the roll:

```text
GMPlan
  -> 是否需要检定
  -> 三档 stakes
  -> 不包含事后结果

DiceEngine
  -> 生成 2d6

GMResolution
  -> 只能解释已生成的结果
  -> 不能改变原始 stakes
```

协议层正式拆分 `GMPlan` 与 `GMResolution`，并在 Schema 和 runtime 层同时验证两阶段边界。

Make `GMPlan` and `GMResolution` first-class schemas and enforce the boundary in both schemas and runtime code.

#### 4.2 建立真正的回合事务 | Define a real turn transaction

完整回合应具有明确边界：

```text
turn_started
  -> model calls
  -> dice
  -> patch validation
  -> actor output
  -> audit
  -> turn_committed
```

区分三类数据：

- **Provisional events**：尚未完成闭环，不构成历史；
- **Committed events**：已成为权威历史；
- **Diagnostic traces**：仅用于调试，不参与状态恢复。

增加 `turn_started`、`turn_aborted`、`turn_committed`，恢复时只承认已经完成闭环的 `turn_committed`。

Separate provisional events, committed events, and diagnostic traces. Add `turn_started`, `turn_aborted`, and `turn_committed`; recovery must trust only completed committed turns.

#### 4.3 幂等性和失败恢复 | Idempotency and failure recovery

每个玩家请求携带 `request_id`、`turn_id`、`attempt_id`。重复提交同一 `request_id` 时，runtime 返回已有结果，不重复掷骰、调用 Actor 或应用 Patch。

Each player request carries `request_id`, `turn_id`, and `attempt_id`. Repeating a `request_id` returns the existing result without rerolling, re-invoking actors, or reapplying patches.

错误策略：

| 故障 | 处理 |
| --- | --- |
| Schema error | 携带 validation error 重试一次 / retry once with the validation error |
| Provider timeout | 指数退避重试 / retry with exponential backoff |
| Auditor rejection | 按违规说明让 Actor 重演一次 / replay the Actor once with the violations |
| 第二次仍失败 | 中止回合，不提交状态 / abort the turn without committing state |
| 进程崩溃 | 从最后一个 committed turn 恢复 / resume from the last committed turn |

#### 4.4 可观察性 | Observability

先输出 JSON Lines，不急于引入完整平台。每次 Agent 调用至少记录：

Start with JSON Lines rather than a full observability platform. Each agent call should record:

```json
{
  "campaign_id": "station-zero",
  "turn_id": "turn-0023",
  "agent": "gm",
  "model": "provider:model",
  "latency_ms": 2310,
  "input_tokens": 4021,
  "output_tokens": 382,
  "retry_count": 0,
  "estimated_cost": 0.0021
}
```

**交付物 / Deliverables：** `v0.2.0`、两阶段 GM 协议、原子回合提交、请求幂等性、可恢复错误路径、Agent 成本报告、50 轮 Fake Agent 稳定性测试和云端 Provider mock 测试。

**验收 / Acceptance：** 连续执行 100 个 Fake Agent 回合，随机注入 Provider 超时、非法 Patch、Audit 拒绝和进程中断；最终不存在重复骰点、重复提交、半完成回合或状态版本错乱。

Run 100 Fake Agent turns with injected timeouts, invalid patches, audit rejections, and process interruptions. There must be no duplicate rolls, duplicate commits, half-completed turns, or version divergence.

### Phase 2：多 Actor 与知识隔离 | Multiple actors and knowledge isolation (`v0.3`)

**目标 / Goal：** 从“GM 加一个 NPC”扩展为真正的多角色 TRPG，同时保持统一协议。

Expand from one GM plus one NPC into a multi-character TRPG without fragmenting the actor protocol.

1. **Actor 实例化 / Actor instances**：保持通用 `ActorAgent` 协议，允许多个独立实例；每个实例配置 `actor_id`、`model_profile`、`character_file`、temperature 和 token 上限。多个角色可以共享同一模型权重。
2. **Knowledge Graph**：将角色知识建模为可追踪事实：`fact_id`、`proposition`、`truth_status`、`source`、`acquired_turn`、`confidence`、`visibility`、`supersedes`。世界真相、角色信念和玩家知识必须分开。
3. **Spotlight Scheduler**：支持 `Player`、`GM`、`Actor`、`Shared`、`Interrupt`；同一 Actor 最多连续两次，直接被问话的角色优先，`Interrupt` 必须有触发理由，GM 裁定后必须交还叙事权。
4. **确定性调度 / Deterministic dispatch**：GM 返回 `primary_actor`、`optional_reactors`、`spotlight_order` 和 `observations_by_actor`；runtime 串行调用 Actor，Actor 不自行决定谁发言。

**退出条件 / Exit gate：** 至少 3 个 Actor 运行 30 轮；没有私有知识串线；角色语言风格可区分；未获 Spotlight 的角色不擅自发言；Shared 场景不退化为单一模型代写所有人。

Run at least three actors for 30 turns with no private-knowledge crossover, distinct voices, no unsolicited speech, and no single-model takeover of shared scenes.

### Phase 3：分支、重生成与回滚 | Branching, regeneration, and rollback (`v0.4`)

**目标 / Goal：** 建立适合 TRPG 的时间线语义，而不是覆盖旧历史。

Introduce explicit timeline semantics instead of overwriting history.

| 操作 | 保持内容 | 新增内容 |
| --- | --- | --- |
| Performance Regeneration | 骰点、State Patch、世界结果 | 仅重生成 Actor 或 GM 的表达 / only regenerated performance |
| Adjudication Regeneration | 玩家输入；默认复用骰点 | 回滚未提交裁定并重新调用 GM / a new uncommitted adjudication |
| Reroll | 玩家输入和既有历史 | 消耗可配置资源，生成新骰点和新分支 / new roll and branch at a resource cost |

事件支持 `campaign_id`、`branch_id`、`parent_branch_id`、`forked_from_event_id`。Branch 只能从历史点继续追加，不能修改旧事件。

Events carry branch identity and can only append from a fork point. Old events remain immutable.

状态通过 `state = replay(snapshot, events, branch_id)` 投影，支持平行剧情、失败场景重试、固定骰点下比较模型和 prompt A/B 测试。

Project state through replay to support parallel stories, retries, fixed-roll model comparisons, and prompt A/B tests.

**交付物 / Deliverables：** Branch Tree、Performance Regeneration、Reroll 资源机制、任意回合回滚和固定骰点模型对比工具。

### Phase 4：场景、剧情框架与长期记忆 | Scenes, story framework, and long-term memory (`v0.5`)

**目标 / Goal：** 从单场景扩展到完整短篇战役，同时不把剧情变成单一路径脚本。

Expand to short campaigns without turning the story into a single forced path.

- **Scene State Machine**：场景包含 `entry_conditions`、`active_pressures`、`available_actors`、`location`、`visible_objects`、`completion_conditions` 和 `possible_transitions`；GM 只能提议切换，runtime 验证目标是否可达。
- **Story Beat**：Beat 是约束和机会，不是固定脚本；一个节点允许多种线索或揭示方式，避免 GM 强推唯一线索。
- **四层记忆 / Four memory layers**：`Canonical State`、`Event Log`、`Scene Summary`、`Retrieved Memories`。优先用 `actor_ids`、`location_ids`、`fact_ids`、`event_types`、`story_beat_ids` 做结构化检索，必要时再引入 embedding。
- **Summary Agent**：只能提出摘要、候选事实、关系变化和未解决线索；所有新事实必须与事件日志交叉验证，摘要不能直接成为 Canonical State。

The summary agent may propose context, but the event log remains the authority for canonical facts.

### Phase 5：HTTP API 与 SillyTavern 适配 | HTTP API and SillyTavern adapter (`v0.6–v0.7`)

**目标 / Goal：** 提供成熟客户端，同时保持核心 runtime 解耦。

Expose a mature client surface without coupling the runtime core to a specific frontend.

先实现原生 FastAPI：

```text
POST /campaigns
POST /campaigns/{id}/turns
GET  /campaigns/{id}/state
GET  /campaigns/{id}/events
GET  /campaigns/{id}/branches
POST /campaigns/{id}/regenerate
POST /campaigns/{id}/reroll
POST /campaigns/{id}/rollback
```

再提供薄的 OpenAI 兼容层：

```text
POST /v1/chat/completions
GET  /v1/models
```

SillyTavern 只看到 `model = trpg-runtime`；内部仍执行完整 agentic loop。SSE 第一版只输出玩家可见的 GM narration、公开骰点和 Actor performance，Debug 信息和私有状态不得进入正文。

SillyTavern should see only `model = trpg-runtime`. The first SSE implementation exposes public narration, public dice, and actor performance only; debug data and private state stay out of the transcript.

客户端 transcript 不等于 authoritative state。用户编辑旧消息时，必须创建分支或触发明确的 runtime 操作，不能静默改写历史。

The client transcript is not authoritative state. Editing an old message must create a branch or invoke an explicit runtime operation.

### Phase 6：云端与本地模型混合 | Hybrid cloud and local models (`v0.8`)

**目标 / Goal：** 验证“云端 GM + 本地 Actor”的最初设想，同时保持 Provider 可替换。

Validate the cloud-GM plus local-actor design through replaceable providers.

统一 Provider 接口，支持：

```text
CloudGMProvider
OpenAICompatibleProvider
KoboldCppProvider
FakeProvider
```

默认分工：

| Agent | 推荐配置 |
| --- | --- |
| GM | 强云端模型、低温度、长上下文、强结构化输出 |
| Actor | 本地 RP 模型、较高温度、局部视图、角色声音优先 |
| Auditor | 便宜的小模型或规则模型、极低温度 |
| Summary | 便宜的长上下文模型 |

需要专门处理本地模型的 JSON 不稳定、Chat/Text Completion 模板差异、正文与结构数据分离、KoboldCpp 超时/断线、Windows 休眠、缺少严格 tool calling 和高温采样导致的 Schema 损坏。

Local integration must address unstable JSON, chat/completion template differences, timeouts, sleep/wake, missing strict tool calling, and schema damage at higher temperatures. A lightweight cloud parser or explicit JSON envelope may be used as a fallback, without granting the local Actor state authority.

### Phase 7：规则系统插件化 | Pluggable rulesets (`v0.9`)

**目标 / Goal：** 从简化 PbtA 演进为可替换规则包，同时不让规则侵入 Agent 和持久化层。

Evolve from minimal PbtA into replaceable rules packages without leaking rules into agents or persistence.

```text
Ruleset
  ├── classify_action()
  ├── validate_check()
  ├── roll()
  ├── resolve_outcome_band()
  ├── validate_consequence()
  └── available_player_resources()
```

首批规则包：

- `pbta-minimal`（默认：`2d6`、`10+` 完全成功、`7-9` 付出代价、`6` 失败）
- `pbta-moves`
- `fate-light`
- `freeform-narrative`

The current minimal PbtA rules remain the default package.

### Phase 8：评估框架与回归测试 | Evaluation and regression (`v1.0`)

**目标 / Goal：** 证明架构改善了 RP，而不是只增加了模型调用次数。

Demonstrate better roleplay control and consistency, not merely more model calls.

自动指标：

| 类别 | 指标 / Metrics |
| --- | --- |
| 权限与知识 / Authority and knowledge | 权限违规率、秘密泄漏率、未经裁定成功率 |
| 状态与剧情 / State and narrative | 状态矛盾率、剧情停滞率、Actor 风格混淆率 |
| 运行成本 / Runtime | 每轮重试次数、延迟、token 和成本 |

固定基准场景至少覆盖：

- 角色被要求使用不知道的信息；
- 玩家偏离主线；
- 预定剧情与骰子失败冲突；
- 两个 Actor 争夺发言权；
- 角色撒谎但世界真相不变；
- Performance Regeneration 不得改变骰点；
- Provider 超时后不得重复提交；
- 从历史分支恢复后状态必须一致。

对照实验：

```text
A. 单一云端模型直接 RP
B. GM + Actor，但没有规则 runtime
C. 完整 TARI runtime
D. 云端 GM + 本地 Actor
```

只有当 C 或 D 在一致性、控制权和体验上有明确优势，项目才证明了自己的价值。

The project proves its value only when C or D shows a clear advantage in consistency, agency, and experience.

## 5. 版本节奏 | Release cadence

| 版本 | 主要范围 / Scope |
| --- | --- |
| `v0.1` | 当前 MVP / current MVP |
| `v0.2` | 回合事务、幂等性、错误恢复 / turn transactions, idempotency, recovery |
| `v0.3` | 多 Actor、Knowledge Graph、Spotlight 调度 / multiple actors, knowledge graph, spotlight scheduling |
| `v0.4` | Branch、Regenerate、Reroll、Rollback |
| `v0.5` | 多场景、Story Beats、长期记忆 / multi-scene stories, beats, long-term memory |
| `v0.6` | FastAPI 与 OpenAI 兼容接口 / FastAPI and OpenAI-compatible API |
| `v0.7` | SillyTavern 基础接入 / basic SillyTavern integration |
| `v0.8` | 云端 GM + 本地 KoboldCpp Actor |
| `v0.9` | 可插拔规则包与评估套件 / pluggable rulesets and evaluation suite |
| `v1.0` | 可稳定完成短篇多场景战役 / reliable short multi-scene campaigns |

版本号是能力里程碑，不是日期承诺。

Version numbers are capability milestones, not calendar commitments.

## 6. 近期优先级 | Immediate priorities

下一轮严格按以下顺序推进：

1. 验证核心 loop，并完成单模型基线对照；
2. 固化 `GMPlan` / `GMResolution`；
3. 实现原子回合事务和 `turn_aborted`；
4. 增加 `request_id` 幂等性；
5. 完善 Agent 失败与 Auditor 重试；
6. 完成 100 回合故障注入验收；
7. 通过 Phase 0/1 退出条件后，再开始多 Actor；
8. 最后接入 SillyTavern 和本地模型。

The next milestone is deliberately reliability-first: validate the core loop, enforce the two-stage GM protocol, make turns atomic and idempotent, test fault recovery, and only then add more actors or clients.

## 7. 成功定义 | Definition of success

TARI 在 `v1.0` 的成功标准是：能够在不静默改写权威历史的前提下，稳定完成多场景短篇战役；玩家拥有清晰的决策权，角色只使用其可见知识，骰点改变真实后果，故障可以恢复，且评估结果持续优于单模型基线。

At `v1.0`, TARI succeeds when it can complete short multi-scene campaigns without silently rewriting authoritative history: player agency is clear, actors use only visible knowledge, dice change real consequences, failures are recoverable, and evaluations consistently beat the single-model baseline.
