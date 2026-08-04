# TARI 三层架构设计 | Three-Layer Architecture

> 状态 / Status: 设计提案（2026-08-04），以下决定已由维护者确认。
> Design proposal; decisions below are confirmed by the maintainer.

## 0. 决策记录 | Confirmed decisions

- **Player agent 采用方案 A**：user 直接驱动玩家角色，意图所有权归 user；"player agent"（角色扮演 agent）统一承担 PC 以外的角色扮演侧。**Option A**: the user drives the player character directly; roleplay agents cover all other characters, and intent ownership stays with the user.
- **Spotlight 策略**：GM 提议 + 运行时策略兜底（无效或缺失提议回退到玩家，回合结束必须归还玩家）。**Spotlight**: GM proposes, runtime policy validates with fallback.
- **延迟对策**：流式打印 GM 的 chain of thought 与角色扮演 agent 的即时输出，让等待过程有反馈；CoT 是 UX 层进度提示，不是权威正文。**Streaming UX**: show GM reasoning and roleplay output as progress; CoT is non-authoritative.

## 1. 三层模型 | The three layers

```text
虚构层 Fiction layer     user（PC 意图） + 角色扮演 agents（第一人称扮演）
      │  声明意图 / 扮演
      ▼
规则层 Rules layer       GM agent（强、可调用工具，只做提案）
      │  检定 / 裁定 / 编排上下文
      ▼
事实层 Truth layer       确定性运行时：骰点、Patch 校验、Spotlight、事件日志、快照
```

**虚构优先（fiction-first）**：user 只需要在虚构层合理扮演，一切行动基于虚构世界；GM 负责把行动转译到规则层（是否检定、用什么 move、stakes），运行时掷骰，GM 依据已知结果裁定，再回到虚构层叙述后果，并作为剧本总负责人推进节奏。

## 2. Agent 定义 | Agent definitions

### 2.1 GM Agent（强 agent）

职责：裁定、请求检定、基于已知骰点结果裁定、叙述、推进场景节奏、提议 Spotlight、为角色扮演 agent 编排上下文投影。

工具分三类：

| 类别 | 工具 | 说明 |
| --- | --- | --- |
| 只读查询 | `search_rules` / `search_world` / `get_character_card` / `get_scenario_outline` | 按需读取规则书、世界设定、角色卡、剧本大纲，避免 context 膨胀；返回内容带引用 |
| 提案 | `request_check` / `propose_patch` / `grant_spotlight` | 产出结构化提案，走现有校验流程 |
| 权威操作 | 无 | 掷骰、提交、快照、Spotlight 归还永远留在运行时 |

工具调用（query + 返回摘要）写入审计事件，GM 的裁定必须可追溯。

### 2.2 角色扮演 Agent（弱 agent / player agent）

- 上下文只含虚构层信息：世界观、人设、故事进度、自己知道的信息（即当前 `ActorView` 的投影语义）
- 输出第一人称的 speech / action / intent + factual claims
- 绝不：发明 user/PC 的意图、宣布未经裁定的结果、使用不可见知识、控制叙事权
- user 直接驱动 PC（方案 A）：user 输入是不可变的虚构层声明

### 2.3 User

整个流程的被服务者；以纯虚构层 Role Play 游玩，对 PC 意图拥有最终所有权。

## 3. Spotlight 策略 | Spotlight policy

1. GM 在 `GMDecision.next_spotlight` 中提议下一所有者；
2. 运行时校验：owner 是否存在、scope 是否允许该 owner、是否违反场景约束；
3. 兜底：提议无效/缺失 → 回退到玩家 `own_action`；回合结束无论中间给了谁，都必须归还玩家；
4. 未来可扩展为策略模块：连续授予次数上限、基于场景的角色可用性等。

## 4. 回合协议（修订）| Turn protocol (revised)

1. Spotlight 在玩家侧，运行时校验 `own_action`；
2. 记录 `player_action_received`（虚构层声明）；
3. GM 规划：可调用只读工具查询规则书/设定/角色卡/大纲，决定是否请求检定 → typed `GMDecision`；
4. 如请求检定：运行时用确定性骰子掷骰并流式播报结果；
5. GM 基于已知骰点结果裁定：补丁提案 + 公开叙事 + Spotlight 提议 + actor 观察；
6. 运行时原子校验并提交 Patch；簿记脚本化（快照、事件、Spotlight 归还策略）；
7. 如授予角色扮演 agent：GM 编排虚构层视图 → agent 流式输出 speech/action → 语义审计 → 发布公开文本；
8. Spotlight 归还玩家，回合完成。

## 5. 流式体验（延迟对策）| Streaming UX

流式通道分两类，**UX 通道不等于权威正文**：

- 进度事件：阶段状态（"GM 正在检索规则书…"、"掷骰 5+2=7 → 成功但有代价"）
- GM CoT：若模型提供 reasoning（如 `deepseek-reasoner` 的 `reasoning_content`），折叠展示为"思考中"面板；**非权威、可开关**，不得作为世界事实进入 SSE 正文或状态
- 角色扮演 agent 的即时 token 流（speech/action 生成即显示）
- 公开叙事的 token 流

安全约束：CoT 可能包含 GM 私有推理（如"隐藏事实 X 暗示…"），因此只能作为瞬态 UI 或 Debug 事件，绝不能进入公开 transcript / replay 正文；Phase 5 SSE 的第一版规则保持"只输出玩家可见内容"。

## 6. 与 SillyTavern 生态的关系

- 角色卡 V2/V3：导入为角色定义（`description`/`personality` → 描述，`first_mes` → 开场，`extensions` → TARI 专属字段）；TRPG 秘密放 sidecar，不进公开卡
- Lorebook 双向映射：数据结构共用（keywords/content/constant），语义保留差异——ST 是关键词注入，TARI 的 knowledge 是权限边界
- OpenAI 兼容端点 + 流式：让 ST 把 TARI 当 `model = trpg-runtime` 接入
- ST 扩展面板：GM 控制台（战役管理、状态检查、事件回放、骰点历史）

## 7. 落地顺序 | Implementation order

1. 角色卡导入器（V2/V3 PNG/JSON → ActorState + sidecar）
2. GM 文档注册与检索层 + 只读查询工具 + 工具调用审计
3. 角色扮演 agent 协议显式化（fiction-only view 的 schema 与投影测试）
4. Spotlight 策略模块（校验 + 兜底 + 可配置策略）
5. 流式通道（进度 / CoT / token 流）+ OpenAI 兼容端点
6. ST 扩展面板

## 8. 对现有代码的改动点 | Code impact

- `domain.py`：GMDecision 工具调用记录；Spotlight 兜底策略；ActorView 已符合 fiction-only 语义
- `runtime.py`：流式事件发射；Spotlight 提议校验与兜底；簿记脚本化
- `agents.py`：PydanticAISuite 增加只读/提案工具；CoT 透出；语言指令沿用 i18n
- `storage.py`：新增事件类型 `tool_called`、流式进度事件
- `rules.py`：SpotlightManager 增加策略校验与 fallback
