# TARI 架构设计 | Architecture

> 状态 / Status: 当前实现基线（2026-09）
>
> 本文描述已经落地的边界，以及下一阶段必须遵守的演进规则。架构目标不是把所有代码抽象成一套“万能 runtime”，而是在共享基础设施的前提下，保持不同叙事产品的状态模型和权威规则独立。

## 1. 设计结论 | Decisions

TARI 由两个业务上下文和一个共享内核组成：

```text
客户端适配层
  CLI / Web HTTP / future OpenAI-compatible or SillyTavern adapter
                 │
                 ▼
┌──────────────────────────────────────────────────────────────┐
│  Campaign Context                  Story Context              │
│  传统可审计 TRPG                    互动小说与 Story Mode       │
│                                                              │
│  CampaignState                      StoryBundle               │
│  TurnOrchestrator                   source compiler            │
│  GM/Actor/Auditor                   NarrativeOrchestrator      │
│  2d6 / Spotlight / Rules            choices / branches         │
│  EventStore                         StoryStore                 │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
                └────────── Shared Kernel ─────┘
                       persistence / llm
```

核心规则：

1. **运行时拥有权威性。** 模型只能返回提案或 prose；状态、骰点、权限、分支和提交由确定性代码控制。
2. **上下文不共享业务状态。** `CampaignState` 与 `StorySessionState` 不互相转换，也不继承同一个“通用状态”基类。
3. **共享基础设施不携带领域语义。** `persistence` 只管理 SQLite 生命周期；`llm` 只管理 OpenAI-compatible 配置、传输和 JSON 解码。
4. **事件追加、快照提交原子化。** 每个上下文拥有独立表和独立存储适配器；同一个 SQLite 文件只是部署便利，不代表业务耦合。
5. **客户端是适配器。** CLI/Web 负责输入输出和资源解析，不直接修改领域状态。

## 2. 共享内核 | Shared kernel

### 2.1 `trpg_runtime.persistence`

`SQLiteStore` 是最小的基础类，只提供：

- 数据库目录创建；
- 连接生命周期；
- commit/rollback/finally 事务边界。

它不创建表，不知道事件 payload，也不暴露任何 Campaign/Story 方法。具体上下文实现自己的 schema：

- `storage.EventStore`：`events`、`snapshots`、`turn_results`；
- `narrative.storage.StoryStore`：`story_events`、`story_snapshots`、`story_branches`、`story_turn_results`。

因此 StoryStore 不再继承 EventStore，避免把传统 Campaign API、表结构和恢复语义隐式带入 Story Mode。

### 2.2 `trpg_runtime.llm`

`llm` 是跨上下文的 provider 基础设施：

- `LLMSettings`：解析 `TARI_LLM_*` 和 evot `EVOT_LLM_*`；
- `OpenAICompatibleClient`：非流式 Chat Completions、超时、认证、可注入测试 transport；
- `extract_json_object`：处理 JSON、Markdown fence 和 Qwen thinking 输出。

领域适配器仍然独立：

- Campaign 使用 `agents.PydanticAISuite`，实现 GM/Actor/Auditor 的多 Agent typed contract；
- Story 使用 `narrative.OpenAINarrativeAuthor`，只把 prose 生成适配为 `NarrativeAuthorProposal`；
- Story compiler 直接使用 `llm.OpenAICompatibleClient`，不依赖 `narrative` 包。

这保证了 provider 传输逻辑统一，同时不把“GM 裁定协议”和“互动小说写手协议”混成一个不明确的接口。未来可以在 `llm` 上增加 provider registry，但不能让 provider 直接获得状态写权限。

## 3. Campaign Context

传统 TRPG 的业务路径保持稳定：

```text
CampaignState
  -> build GM/Actor projections
  -> GM plan
  -> deterministic DiceEngine
  -> GM resolution proposal
  -> patch / spotlight validation
  -> optional Actor + Auditor
  -> EventStore atomic commit
```

主要模块：

- `domain.py`：CampaignState、GMDecision、ActorTurn、Patch、Spotlight 等协议；
- `rules.py`：2d6、结果档位、Patch 和 Spotlight 权限；
- `agents.py`：PydanticAI/Fake Agent adapter；
- `runtime.py`：`TurnOrchestrator` 和回合状态机；
- `storage.py`：追加式事件、快照、回合结果和恢复支持。

Campaign 的下一步仍是可靠性加固，而不是立即接入更多客户端：GMPlan/GMResolution 的严格边界、故障注入、快照重建和多 Actor 都属于该上下文内部演进。

## 4. Story Context

Story Mode 包含离线资源编译和在线互动运行时两部分，共享 `story.bundle` 数据契约：

```text
TXT/Markdown source
  -> source structure plan
  -> chapter cards
  -> rolling story arcs
  -> world knowledge / structures
  -> immutable StoryBundle + evidence
                              │
                              ▼
       NarrativeOrchestrator + StoryStore
       player identity / choice / freeform / branch
```

### 4.1 编译侧

- `story.importer`：无损、确定性的 source scaffold；
- `story.decomposer`：可恢复的语义编译器；保存源 SHA、章节 SHA、settings fingerprint 和阶段 checkpoint；
- 原始源文件永不改写；source plan 只保存行号边界；
- LLM 只生成结构化中间产物，归一化和 Bundle 引用校验由 Python 完成；
- 编译失败按阶段保留可续跑的 manifest 和 failure 信息。

编译器的事实边界是“source-referenced semantic resource”，不是无证据的世界真相数据库。实体、事实、关系和剧情结构必须保留 source refs；模型推断不能绕过 Bundle 校验成为运行时权威。

### 4.2 运行时

- `story.bundle`：StoryBundle、StoryBeat、Choice、CanonFact、Evidence 等不可变输入；
- `narrative.domain`：玩家身份、会话状态、输入、写手提案和回合结果；
- `narrative.runtime`：选择解析、窄状态 patch、事实揭示、节拍推进和事务提交；
- `narrative.storage`：事件、快照、分支和 request-id 幂等；
- `narrative.providers`：把共享 LLM 文本客户端适配为 Story 写手。

单次回合的权威流程：

```text
player input
  -> runtime resolves available choice
  -> author writes prose only
  -> runtime validates beat / choices / reveals / patches
  -> StoryStore atomically commits events + snapshot + result
```

自由行动不会自动推进节拍；选择的目标节拍、choice effects 和 terminal 状态来自 Bundle，不能由模型改写。分支只追加子时间线，不修改父快照。

## 5. 适配层 | Adapters

- `cli.py`：旧 Campaign 命令和 Story Mode 命令的命令行适配器；
- `web/app.py`：当前本机 Web 控制台、Campaign API 和 Story API；
- `resource_library.py`：素材发现、校验和 Story Bundle 注册；
- `story`/`narrative` 的 workflow 函数：给脚本和测试使用的无 Typer façade。

Web 的 Story API 是 vertical slice，不是完整客户端：它提供资源、session、turn、events、branches，但没有专用 Story Mode 页面，也不是 SillyTavern/OpenAI-compatible public adapter。公开暴露前必须增加认证、限流和传输安全。

## 6. 依赖方向 | Dependency direction

允许的依赖方向：

```text
cli/web
  -> context application APIs
  -> Campaign or Story domain/runtime
  -> shared persistence / llm

story compiler -----------------------> shared llm
narrative provider adapter -----------> shared llm + story.bundle
Campaign agent adapter ---------------> Campaign domain + pydantic-ai
```

禁止：

- `story.decomposer` 依赖 `narrative.providers`；
- `StoryStore` 继承或调用 `EventStore` 的业务方法；
- provider、Web handler 或 CLI 直接写 SQLite 表；
- 模型输出直接修改 `CampaignState`、`StorySessionState` 或 StoryBundle；
- 为了“统一”而创建同时包含 Campaign 和 Story 字段的 God object。

## 7. 当前删除与保留边界 | Cleanup boundary

本轮删除了只服务 Odyssey 一次运行的 detached launcher、merge probe、status 和 bundle verifier。它们属于实验运维记录，不是产品 API；最终 Odyssey 产物保留在运行时数据目录，不把一次性脚本继续固化为架构。

保留的旧代码不是残留垃圾：

- Campaign runtime/agents/rules/storage 仍是现有 TRPG 产品路径；
- `materials/foreverse/extract_worldinfo.py` 是文档明确的可复用素材转换工具；
- Story importer 与 semantic compiler 是当前重点 feature；
- Story API 测试和 compiler 测试是回归门禁。

待清理但暂不删除：

- Web app 内的旧 Campaign 路由应拆成 router/service，避免继续增长为单文件 God module；
- `narrative.workflow` 的 compile façade 后续可迁移到独立 application service，保留现有公开导入路径作为兼容层；
- Campaign 与 Story 的事件 replay/projection 目前仍是两套实现，只有在语义对齐后才抽象共享 replay port；
- 旧 CLI/API 的公开兼容性在没有迁移测试前不得删除。

## 8. 完成定义 | Definition of done

任何跨上下文改动必须同时满足：

1. 领域状态和权威协议没有跨边界泄漏；
2. 新基础设施有独立单元测试或被两个上下文的回归覆盖；
3. `pytest` 全量通过；
4. `ruff check .` 全量通过；
5. `git diff --check` 通过；
6. Story compiler、Story runtime、Campaign runtime 至少各有一条 smoke/regression path；
7. 说明文档和 roadmap 不宣称尚未实现的客户端或可靠性能力。
