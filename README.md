# TARI

> **从原著素材到连续故事，玩家在关键处参与**<br>
> **Source-grounded stories with meaningful, occasional player direction**

## 项目简介 | Overview

TARI 正在转向以故事阅读和低频共创为中心的产品：从书籍中提取人物、世界与冲突，让 LLM 持续创作有因果联系的场景，玩家在关键节点决定方向。状态、来源、分支和发布由运行时管理。

TARI is moving toward continuous, source-grounded storytelling with occasional player direction. The runtime owns state, provenance, branches, and publication; the writer focuses on prose.

2026-09-16 的[产品方向](docs/product-direction.md)和[技术路线图](docs/technical-roadmap.md)是当前工作依据。传统多 Agent TRPG 作为既有实验模式保留，停止功能扩展。

## 当前版本 | Current status

当前仍是 **v0.1 原型**。已具备可恢复拆书编译、Story Bundle、CLI/HTTP 会话和分支接口。Story 提交已增加版本冲突保护；真实写手只返回正文，runtime 执行选择后果；写手输入包含身份、关系、公开历史与可见事实。

已新增[连续阅读](docs/reading-mode.md)：一次生成有限场景，在关键决定处暂停；同一请求可恢复已完成正文，新会话绑定故事包内容版本。可用原创样本 `examples/story/last_ferry.yaml` 体验一次介入后的两种走向。

完整的低频共创体验仍在建设：当前自动编译结果主要沿情节弧推进，尚无动态场景规划或专用阅读界面。场景图中的有意义分歧目前需要作者设计；文学质量需单独试读评估。

The current release has resumable compilation, bounded and resumable scene continuation, and Story CLI/HTTP APIs. Dynamic scene planning and a dedicated reading UI remain planned work.

## 既有 Campaign 规则 | Existing Campaign rules

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
- 可恢复的语义拆书编译：章节卡、情节弧、世界知识、结构大纲和审计产物 / resumable semantic source compilation with chapter cards, arcs, world knowledge, structures, and audit artifacts
- Story Mode：Story Bundle、选择节拍、玩家身份、分支和原子叙事事件 / Story Mode bundles, beats, identities, branches, and atomic narrative events
- OpenAI-compatible 本地/远程写手，以及 Story Mode CLI 和 HTTP vertical slice / OpenAI-compatible local or remote prose author plus Story Mode CLI and HTTP vertical slice

## 互动叙事 | Interactive narrative

Story Mode 与传统 TRPG 的 `CampaignState` / `TurnOrchestrator` 分开，先用离线 Fake Author 验证“故事包 → 玩家身份 → 选择 → 状态 → 分支”的闭环。示例故事包位于 `examples/story/lantern_gate.yaml`：

```bash
trpg story-new examples/story/lantern_gate.yaml --session-id lantern-demo --player-name Ari
trpg story-play examples/story/lantern_gate.yaml lantern-demo --author fake
trpg story-branch lantern-demo hesitation
trpg story-play examples/story/lantern_gate.yaml lantern-demo --branch-id hesitation --author llm
```

`story-play` 默认使用离线 Fake Author；传入 `--author llm` 会调用 `TARI_LLM_*`，未设置时回退到 `~/.evotai/evot.env` 中 evot 当前的 OpenAI-compatible provider。输入选择编号或 `choice_id` 会推进节拍；普通文本是自由行动，不会绕过选择自动推进；`/continue` 重写当前节拍；`/branch NAME` 从当前状态创建并切换到子分支；`/quit` 退出。Story Mode 使用与传统战役相同的 SQLite 文件，但事件、快照和分支表独立保存。

See [Story Mode design and extension points](docs/story-mode.md) for the bundle schema, author contract, state-patch boundary, local LLM configuration, and end-to-end validation.

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
# 把 DeepSeek API key 填入 .env（CLI 会自动加载）
trpg new examples/station_zero.yaml
trpg play station-zero
```

模型标识配置在 `config/agents.yaml` 中。PydanticAI 支持带 provider 前缀的模型标识；凭据必须放在环境变量中。

Model identifiers are configured in `config/agents.yaml`. PydanticAI accepts provider-qualified model identifiers; keep credentials in environment variables.

## 本地化 | Localization

目前原生支持英文和简体中文。创建战役时用 `--lang` 选择语言；未指定时使用场景文件的 `default_locale`（默认为 `en`）。语言会存入战役状态，之后游玩全程——GM、NPC、Auditor 和 CLI 界面——都会使用该语言。

```bash
trpg new examples/station_zero.yaml --lang zh
trpg play station-zero
```

场景文件通过 `localizations:` 段提供多语言内容。新增语言时，在 `src/trpg_runtime/i18n.py` 注册语言代码，并补充对应的界面字符串与场景翻译即可。

The runtime natively supports English and Simplified Chinese. Pass `--lang` when creating a campaign; the language is stored in the campaign state and used for the whole session. Scenario files carry per-locale content under `localizations:`. To add a language, register it in `src/trpg_runtime/i18n.py` and provide UI strings plus scenario translations.

## CLI 命令 | CLI commands

```text
trpg new SCENARIO [--campaign-id ID] [--seed N] [--fake]
trpg new SCENARIO --world-info worldinfo.json   # 合并 SillyTavern 世界书
trpg import-card CARD [--sidecar SIDECAR] [--output FILE] [--seed N] [--campaign-id ID] [--show]
trpg export-lorebook CAMPAIGN_ID [--output FILE]  # 导出为 SillyTavern world info
trpg play CAMPAIGN_ID [--debug] [--fake]
trpg story-import SOURCE [--output FILE] [--story-id ID]
trpg story-compile SOURCE --output-dir DIR [--story-id ID]  # 可恢复拆书编译
trpg story-new BUNDLE --session-id ID
trpg story-play BUNDLE SESSION_ID [--author fake|llm]
trpg story-read BUNDLE SESSION_ID [--scenes 3] [--choice ID] [--request-id ID] [--author fake|llm]
trpg story-branch SESSION_ID BRANCH_ID
trpg inspect-state CAMPAIGN_ID [--all]
trpg inspect-events CAMPAIGN_ID
trpg replay CAMPAIGN_ID
```

默认数据存储在 `runtime-data/trpg.db`，可通过 `TRPG_DB_PATH` 覆盖。

Data is stored in `runtime-data/trpg.db` by default. Override it with `TRPG_DB_PATH`.

## Web 控制台 | Web console

`trpg web` 启动一个本机 Web 控制台（FastAPI + 无构建原生前端），一条命令即可浏览素材、组合战役并游玩：

```bash
trpg web                        # http://127.0.0.1:8765
trpg web --port 9000 --no-open  # 指定端口且不自动打开浏览器
```

支持：

- 素材库：自动扫描 `examples/`、`materials/foreverse/`、`runtime-data/resources/`，可用 `TARI_RESOURCE_DIRS` 追加素材目录；网页可直接上传角色卡（PNG/JSON）、世界观（JSON）、剧本（YAML）。角色卡指狭义的单角色设定（如「墨笔」）；多 NPC 世界卡与独立世界书统一归入「世界观」并自动去重，避免同一世界重复出现。
- 组合器：自由组合剧本、角色卡、世界观、规则预设/自定义规则文本，创建前实时预览。
- 游玩页：完整回复 + 阶段进度提示（GM 思考中/掷骰等）、骰子徽章、GM 视图开关；Token 级流式输出暂缓，后续再启用。
- 设置页：编辑 `config/agents.yaml` 的模型与 Agent 参数；API key 状态只读（仍放在 `.env`）。

默认只监听 `127.0.0.1`，不做鉴权。`--host 0.0.0.0` 可让局域网访问，但会暴露控制台与战役数据，请自行评估风险。

`trpg web` starts a local web console with a no-build frontend: browse resources, compose campaigns, and play in the browser. It listens on `127.0.0.1` by default without authentication; `--host 0.0.0.0` exposes the console to the LAN at your own risk.

## 角色卡导入 | Character card import

支持导入 SillyTavern 生态的 Character Card V2/V3（PNG 或 JSON 文件），自动映射为可游玩的场景：

```bash
trpg import-card path/to/Seraphina.png --output examples/seraphina.yaml
trpg new examples/seraphina.yaml
trpg play seraphina
```

字段映射：`name` → 角色名，`description`/`personality` → 角色描述，`first_mes` → 开场白，`character_book` → 角色知识，`extensions.tari` → TARI 专属字段（goals/attributes/knowledge）。可选 `--sidecar` 提供 GM 专属数据（secrets、goals、场景隐藏事实等），这些数据不会写入公开卡文件。

It imports SillyTavern Character Cards (V2/V3, PNG or JSON) into playable scenarios: `name` → actor, `first_mes` → opening, `character_book` → knowledge, plus an optional GM-only sidecar for secrets and scene facts.

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

上图是传统 Campaign 路径。Story Mode 不在其中：它是另一个 bounded context，拥有自己的状态、事件表和分支语义，只与 Campaign 共享 `persistence` / `llm` 基础设施：

```text
TXT/Markdown -> source plan -> chapter cards -> rolling arcs -> world knowledge
             -> structures -> StoryBundle(+evidence)   # 离线、可恢复
StoryBundle -> NarrativeOrchestrator -> choice/freeform 解析
             -> author 只写 prose -> 运行时校验 beat/reveal/patch
             -> StoryStore 原子提交事件+快照+幂等结果     # 在线
```

完整边界、依赖方向与禁止事项见 [架构 | Architecture](docs/architecture.md)；回合协议见 [协议 | Protocol](docs/protocol.md)；本地 HTTP 控制台当前的 unprotected 面与公开前提见 [安全 | Security](docs/security.md)。

## 当前边界 | Current limitations

- 一个玩家、一个场景和一个获得 Spotlight 的 NPC Actor / one player, one scene, and one spotlighted NPC actor
- 尚无战斗系统或完整 PbtA move catalog / no combat system or full PbtA move catalog
- 语义 Auditor 的质量取决于配置的模型 / semantic Auditor quality depends on the configured model
- LLM 文本不确定，即使骰点是确定的 / LLM text is not deterministic, even when dice are
- Replay 校验已记录骰点和事件顺序，但不会生成完全相同的 prose / replay verifies recorded dice and event ordering, but does not regenerate identical prose
- 已有通用本机 Web 控制台；尚无 Story Mode 专用页面或 SillyTavern 适配器 / a general local Web console exists; no dedicated Story Mode page or SillyTavern adapter yet

## 技术路线 | Roadmap

详见[技术路线图](docs/technical-roadmap.md)：

1. **S0**：Story 提交、来源、素材版本与恢复的可信基础（进行中）。
2. **S1**：将原著知识加工为可持续创作的场景。
3. **S2**：连续性记忆、有预算的续写、关键节点介入。
4. **S3**：专用阅读与介入界面。
5. **S4**：基于真实试读，迭代质量、等待时间与成本。

历史 Campaign 命令继续保留。上面的命令与能力说明描述现有实现，不代表多 Agent 即时互动仍是优先方向。

## 许可证 | License

MIT
