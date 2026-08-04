# TARI

> **可审计、多 Agent 的 TRPG 运行时**<br>
> **An auditable, multi-agent TRPG runtime**

## 项目简介 | Overview

TARI 用明确的权限边界把玩家、GM、NPC Actor、规则引擎和事件存储分开，目标是在保留自由叙事的同时，让世界事实、随机结果和角色知识可验证、可恢复。

TARI separates the player, GM, NPC actors, rules engine, and event store behind explicit authority boundaries. Its goal is to preserve freeform storytelling while making world facts, random outcomes, and character knowledge verifiable and recoverable.

单一模型同时扮演世界、裁判、旁白和所有角色时，可能悄悄改写事实或夺取叙事控制权。TARI 的职责划分是：

TARI addresses the failure modes of a single model acting as the world, referee, narrator, and every character:

- **玩家 | Player**：决定玩家角色的意图 / decides the player character's intention.
- **GM Agent**：提出检定和世界后果 / proposes checks and world consequences.
- **Actor Agent**：表现一个 NPC 的台词和意图行动 / performs one NPC's speech and intended action.
- **规则运行时 | Rules runtime**：拥有骰点、权限、Spotlight 和状态提交权 / owns dice, permissions, spotlight, and state commits.
- **事件存储 | Event store**：记录世界如何演变到当前状态 / records how the world reached its current state.

## 当前版本 | Current status

当前是 **v0.1 MVP**，重点不是立即增加功能，而是验证核心 loop 是否比单模型 RP 更稳定。下一阶段优先补可靠性、可测试性和故障恢复，再扩展多角色，最后接入 SillyTavern 与本地模型。

The project is currently at **v0.1 MVP**. The immediate priority is to verify that the core loop is more stable than a single-model RP baseline. Reliability, testability, and recovery come before additional actors, SillyTavern, or local models.

完整路线见 [技术路线图 | Technical Roadmap](docs/technical-roadmap.md)。

## MVP 规则 | MVP rules

检定使用刻意保持简洁的 PbtA 风格 `2d6`：

Checks use an intentionally minimal PbtA-style `2d6` result:

- `10+`：完全成功 / full success
- `7-9`：成功但付出代价 / success with a cost
- `6 或更低`：失败 / failure

当前 MVP 没有难度等级和修正值。

The MVP has no difficulty classes or modifiers.

## 当前能力 | Current capabilities

- CLI 游戏循环 / CLI play loop
- PydanticAI GM、Actor 和语义 Auditor 适配器 / PydanticAI GM, Actor, and semantic Auditor adapters
- 离线 Fake Agent 测试和演示 / offline Fake Agent tests and demos
- 显式 Spotlight 所有权 / explicit spotlight ownership
- 按 Actor 隔离的知识投影 / actor-specific knowledge projection
- 带 seed 的确定性 `2d6` / deterministic seeded `2d6`
- 结构化且可校验的状态 Patch / structured, validated state patches
- 追加式 SQLite 事件日志 / append-only SQLite event log
- 战役快照和恢复 / campaign snapshots and resume
- 独立的 provider、model、agent YAML 配置 / independent provider, model, and agent YAML configuration
- 不影响普通游玩的 Debug trace / debug traces outside normal play

## 快速开始 | Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
pytest

trpg new examples/station_zero.yaml --fake
trpg play station-zero --fake --debug
```

使用真实云端 Agent：

For real cloud agents:

```bash
cp .env.example .env
export OPENAI_API_KEY=...
trpg new examples/station_zero.yaml
trpg play station-zero
```

模型标识配置在 `config/agents.yaml` 中。PydanticAI 支持带 provider 前缀的模型标识；凭据必须放在环境变量中。

Model identifiers are configured in `config/agents.yaml`. PydanticAI accepts provider-qualified model identifiers; keep credentials in environment variables.

## CLI 命令 | CLI commands

```text
trpg new SCENARIO [--campaign-id ID] [--seed N] [--fake]
trpg play CAMPAIGN_ID [--debug] [--fake]
trpg inspect-state CAMPAIGN_ID [--all]
trpg inspect-events CAMPAIGN_ID
trpg replay CAMPAIGN_ID
```

默认数据存储在 `runtime-data/trpg.db`，可通过 `TRPG_DB_PATH` 覆盖。

Data is stored in `runtime-data/trpg.db` by default. Override it with `TRPG_DB_PATH`.

## 架构 | Architecture

```text
CLI
  -> TurnOrchestrator
       -> GM Agent proposal
       -> Rules validation
       -> DiceEngine (2d6)
       -> GM resolution proposal
       -> atomic state commit
       -> SpotlightManager
       -> Actor Agent proposal
       -> semantic audit
       -> public transcript
  -> SQLite EventStore + snapshots
```

运行时不会把模型 prose 直接当成状态；结构化 Patch 必须通过校验后才能提交。

The runtime never treats model prose as authoritative state. Structured patches must pass validation before they are committed.

详见 [架构 | Architecture](docs/architecture.md)、[协议 | Protocol](docs/protocol.md) 和 [安全 | Security](docs/security.md)。

## 当前边界 | Current limitations

- 一个玩家、一个场景和一个获得 Spotlight 的 NPC Actor / one player, one scene, and one spotlighted NPC actor
- 尚无战斗系统或完整 PbtA move catalog / no combat system or full PbtA move catalog
- 语义 Auditor 的质量取决于配置的模型 / semantic Auditor quality depends on the configured model
- LLM 文本不确定，即使骰点是确定的 / LLM text is not deterministic, even when dice are
- Replay 校验已记录骰点和事件顺序，但不会生成完全相同的 prose / replay verifies recorded dice and event ordering, but does not regenerate identical prose
- 尚无 Web UI 或 SillyTavern 适配器 / no web UI or SillyTavern adapter yet

## 技术路线 | Roadmap

路线遵循“先证明核心 loop，再加固可靠性，之后扩展角色和客户端”的顺序。路线图中的每个阶段都有退出条件，不以增加调用次数代替质量提升。

The roadmap follows “prove the core loop first, harden reliability next, then expand actors and clients.” Each phase has an exit gate; more model calls are not a substitute for quality.

主要里程碑：

Key milestones:

1. **v0.2**：回合事务、幂等性、错误恢复和可观察性 / turn transactions, idempotency, failure recovery, and observability
2. **v0.3-v0.5**：多 Actor、知识图谱、Spotlight 调度、分支和长期记忆 / multiple actors, knowledge graph, spotlight scheduling, branches, and long-term memory
3. **v0.6-v0.8**：原生 HTTP API、SillyTavern 基础接入和云端 GM + 本地 Actor / native HTTP API, basic SillyTavern integration, and cloud GM plus local actors
4. **v0.9-v1.0**：可插拔规则包、评估框架和可稳定完成的多场景短篇战役 / pluggable rulesets, evaluation, and stable multi-scene short campaigns

## 许可证 | License

MIT
