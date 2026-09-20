# 自由段与 append-only 上下文 | Freeform Segments

> 设计文档。本文定义 TARI 从"选项分支"转向"间歇式深度扮演"的契约。产品意图见
> [product-direction.md](product-direction.md)；本文只管实现契约与不变量。

## 1. 目标形态

叙事在两种模式间循环：

| 模式 | 谁掌权 | 内容 |
| --- | --- | --- |
| 长篇段 | 模型 | 连续写多节，角色按自身逻辑行动，玩家只读 |
| 自由段 | 玩家 | 在一个张力内自由行动，多轮往返，行动真实生效 |

自由段的入口是**一个真正待决的张力**；出口是**这个张力被解决**——用玩家的方式，而不是预设的方式。
张力解决后退回长篇段。

自由段的价值不是"能输入文本"，而是**解法空间从"枚举"变成"开放"**：玩家可以想出作者没预写过的路。

## 2. 不变量 | Invariants

1. **事实权威在 runtime**：LLM 输出的是提议；只有 runtime 提交的才算发生。
2. **语义裁量在 LLM**：世界内的合理性（古代没有手枪）由模型判断，不写判定代码。
3. **前缀不可变**：prompt 一旦写下就不再修改，新信息只能追加到末尾。
4. **否决不提交状态**：裁量不通过时只产出正文，状态零变化。
5. **裁量要落库**：裁量结果与出口判定写入事件流，重放时读取而非重算。
6. **分支隔离**：一条分支的事件不进入另一条的上下文。
7. **每轮一个事务**：自由段的每一轮都是独立原子提交，中途失败不留半个回合。

## 3. 上下文结构（cache 优先）

prompt 由三段构成，顺序固定：

```text
[1 静态前缀]  系统提示词 + 世界档案 + 会话初始状态   ← 一次写下，永不修改
[2 只增段]    事件历史：玩家行动 / 裁量结果 / 正文    ← 每轮追加到末尾
[3 本轮]      当前行动 + 裁量指令                    ← 只有这一段是新的
```

**规则**：任何改动都只能发生在 [3]，或追加到 [2] 的末尾。[1] 与 [2] 已有部分永不改写。

### 3.1 世界档案（静态前缀的内容）

由编译产物一次性生成，存为 bundle 的一部分（`world_profile`）。包含：

- 故事设定与时代约束（用于裁量"这个行动成立吗"）
- 人物表：会什么、不会什么、核心性格
- 关键事实（从 canon facts 中挑选的稳定部分）
- 风格约束

**不在运行期做检索**。运行期需要新信息时，把它追加到 [2] 末尾（如"林岑第一次看清那人的脸：……"），已追加的不删除。

### 3.2 需要消除的现有违规

| 位置 | 问题 |
| --- | --- |
| `narrative/context.py` `history[-12:]` | 滑动窗口，每轮改写前缀 |
| `narrative/context.py` facts 集合运算 | 集合无序，顺序变化即前缀失效 |
| `narrative/runtime.py` `recent[-12:]` | 同上，喂给写手的事件被截断 |
| `narrative/context.py` 每轮重建 dict | 结构本身就不是追加式的 |

### 3.3 compaction

当 [1]+[2] 逼近模型上限（默认阈值 0.7）时执行一次 compaction：

1. 把 [2] 中最旧的一段（默认保留最近 12 轮）压缩成一段结构化摘要
2. 用摘要替换被压缩部分，形成新的 [1]
3. 之后的轮次在新前缀上继续追加

compaction 必然让缓存失效一次；这是接受的代价。执行后前缀重新稳定。

## 4. 数据结构

```python
class FreeformAction(BaseModel):
    turn: int
    player_input: str
    feasible: bool
    reason: str                 # 世界内解释，用于呈现
    world_response: str         # 世界如何回应（否决时也必须有）
    patches: list[NarrativeStatePatch]
    tension_resolved: bool

class FreeformSegment(BaseModel):
    segment_id: str
    entry_beat_id: str
    tension: str                # 入口张力：此刻悬着什么
    stakes: str                 # 代价是什么（写进 prompt，防止被淡忘）
    status: Literal["open", "resolved"] = "open"
    actions: list[FreeformAction] = []
    resolution: str = ""        # 张力如何被解决

class ActionRuling(BaseModel):
    """LLM 裁量输出。可行与不可行都必须给 world_response。"""
    feasible: StrictBool
    reason: str
    world_response: str
    patches: list[NarrativeStatePatch] = []
    tension_resolved: StrictBool = False
    resolution: str = ""
```

`StorySessionState` 新增 `active_segment: FreeformSegment | None`。

## 5. 单轮协议

```text
玩家输入行动
  → runtime 组装 messages：[1] + [2] + [3 本轮行动]
  → LLM 返回 ActionRuling（结构化）
  → runtime 校验：
       patches 路径白名单（复用 _apply_state_patches）
       patch old_value 一致性
       tension_resolved 只能从 false 变 true
  → 可行：写入 state patches，追加事件（行动 / 裁量 / 正文 / 状态）
    不可行：只追加事件（行动 / 裁量 / 正文），状态零变化
  → tension_resolved=true：关闭 segment，退回长篇段
  → 原子提交
```

**一次调用完成裁量与正文**：裁量的 `world_response` 就是这一段正文，避免"先判定再写"两次调用。

## 6. 裁量规范

### 6.1 锚点

裁量必须对照静态前缀中的世界档案，不得凭常识漂移。提示词要求模型：

- 先检查行动在此世界设定下是否成立
- 不通过时解释**为什么**（用于世界内呈现），不要把主角能力外的东西塞给玩家

### 6.2 否决的呈现

否决必须伪装成世界的一部分，不能是系统提示。

| 错误 | 正确 |
| --- | --- |
| 「该行动不符合世界观，请重试」 | 「林岑的手在腰间摸索——那里只有一柄小刀，别的什么都没有。」 |

要求：
- 否决给出**信息而非禁令**（告诉玩家"这里有什么"，而不是"你不能做什么"）
- 否决**可以推进张力**（玩家试着做做不到的事，同时周伯已经开始解缆绳）

### 6.3 过度配合的处理

不拦截玩家的破坏性选择。但**后果必须一致地生效**：世界如实记住并延续。这是"世界有记忆"，不是"拦截玩家"。

### 6.4 角色逻辑

玩家让角色做违背人设的事时**不拒绝执行**，而是让后果体现角色张力（关系变化、他人反应）。
模型不在正文里说"这不像你会做的事"。

## 7. 出口

出口条件是**张力被解决**，不是动作次数或位置。

- 由 LLM 提议（`tension_resolved`），runtime 记录
- 玩家可显式宣布结束（`/done`），由 runtime 直接关闭 segment
- 出口判定必须落库（`story_segment_resolved` 事件），重放时读取

关闭 segment 时：写入 `resolution`，`StorySessionState.active_segment = None`，控制权交回长篇段。

## 8. 收敛

自由段的行动会偏离原著基线。收敛靠**软约束**而非硬规则：

- 静态前缀里的世界档案承担"什么是不变的"
- 已提交的事实进入只增段，模型看得见自己造成的结果
- 冲突时优先兑现玩家行动，重排原著情节而不是取消它

## 9. 与现有代码的映射

| 现有 | 改动 |
| --- | --- |
| `narrative/context.py` | 改为 `narrative/session_context.py`：append-only 消息序列器 |
| `narrative/runtime.py` | 新增 `process_freeform_action`；`recent[-12:]` 改为事件流全量投影 |
| `narrative/providers.py` | 新增 `rule_action`（返回 `ActionRuling`） |
| `narrative/domain.py` | 新增 `FreeformSegment` / `FreeformAction` / `ActionRuling` |
| `story/bundle.py` | 新增 `world_profile`（静态前缀，可缺省） |
| `story/decomposer.py` | 新增世界档案产出阶段 |
| `cli.py` | 新增 `trpg story-act` |

`input_mode="freeform"` 的语义改变：**在自由段内允许改变状态**；在没有 active segment 时仍拒绝推进 beat（保持现有安全行为）。

## 10. 验收标准

1. 在 `last_ferry.yaml` 的抉择点进入自由段
2. 连续多轮自由行动，行动真实改变状态（可在快照中验证）
3. 不合理行动被世界内回应，且状态零变化
4. 张力解决后收敛回长篇段，resolution 落库
5. 连续多轮的 prompt 前缀稳定（可测量：前缀长度与内容不变）
6. 全量门禁通过

## 11. 明确不做

- 不做并发控制（单玩家单会话）
- 不做向量检索（静态前缀 + 追加够用）
- 不做工具调用循环（阶段 1 不需要）
- 不为 Campaign 侧改动

已有保护不回退：`request_id` 幂等、版本冲突校验、事务原子性继续保留。

## 12. 实现记录与实测

### 12.1 实现中发现的两个硬约束

**prompt 只能有一条 system 消息，且必须在最前。**
llama.cpp 的 Qwen3.8 chat template 对非首位 system 直接返回 HTTP 500
（`System message must be at the beginning`）。实测确认：

| 消息结构 | 结果 |
| --- | --- |
| 1 条 system + user | 200 |
| 2 条 system 都在开头 | **500** |
| system 夹在中间 | **500** |
| 连续多条 user | 200 |

因此静态前缀合并为**单条** system；事件投影只产出 user / assistant，
世界内的簿记以「（旁白）…」形式作为 user 消息进入。

**会变化的状态不能进前缀。**
`state.variables` 每轮都变。若放进前缀，第一轮之后前缀就变，缓存永远不命中。
现改为只使用会话创建时冻结的 `initial_variables`；增量由事件投影承载。
这一条是被测试当场抓出来的，不是设计时想到的。

### 12.2 缓存实测（llama.cpp，Qwen3.8-27B-visual）

固定前缀约 1647 tokens，四次请求：

| 场景 | prompt_tokens | prompt_ms | cache 命中 tokens |
| --- | --- | --- | --- |
| 冷启动 | 1647 | 2405 | 0 |
| 完全相同 | 1647 | **233** | 1643 |
| 仅末尾变化 | 1647 | **354** | 1635 |
| 在前缀之上追加 | 1664 | 480 | 1643 |

命中后 prefill 由 2405 ms 降到 233 ms，**约 10 倍**；仅改末尾时命中率 99.3%。
对照旧实现（`history[-12:]` + 每轮重新筛选 facts）每轮都等于冷启动。

### 12.3 真实模型端到端验收（last_ferry，原创样本）

在抉择点（`stay` / `cross`）进入自由段，玩家输入：
**"我把信交给周伯，托他带过河，自己留下帮阿禾"** —— 两个预设选项之外的路。

结果：

- 裁量判定成立，`reason` 指出该行动**同时满足两个方向的意图**；
- 正文完整兑现：把信交给周伯 → 转身跑回 → 肩抵门板把锈死的门推开 → 安置孩子；
- 产生的后果是模型**自己命名**的变量，不在任何预设里：
  `variables.letter_status = "in_transit"`、`variables.lin_cen_location = "old_waiting_room"`；
- 张力被判定解决并落库，`active_segment` 关闭，choices 交回长篇段。

否决路径用「我掏出一把手枪，对准周伯」验证：

- `feasible=false`，`reason` 明确引用**世界档案**（未提及持有枪械，角色也不具备）；
- `consequences` 为空，状态**零变化**；
- 正文是世界内回应而非系统提示：「他下意识地伸手探向腰侧，指尖触到的却只有
  那封被雨水微湿的信纸和几枚硬币，并没有冰冷的金属触感。」

已知瑕疵：否决那一轮的正文第二段复述了上一轮已发生的事件（周伯离岸、阿禾递毛巾）。
这是模型的复述倾向，不是架构问题；自由段目前不做发布前审查。

### 12.4 未完成

- 自由段尚未接入 HTTP 与 Web 界面（只有 CLI）
- compaction 只实现了阈值判定，未实现压缩动作
- `narrative/context.py`（choice 模式）仍在用 `history[-12:]`，未迁移到本模块
- 自由段不做发布前审查，正文可能复述已完成事件（实测出现过，见 12.5）

### 12.5 第二轮实测：状态快照闭环与三处提示词修复

加入状态快照（runtime 在每次提交后把**完整权威状态**写入 `story_state_snapshot` 事件，
投影为「（旁白）当前状态：…」）后的复测，暴露并修复了三处提示词缺陷：

| 缺陷 | 表现 | 修复 |
| --- | --- | --- |
| 张力提示里塞了预设选项 | 自动推断的张力带上了 "可能的走向：留下…；渡河…"，引导裁量模型拿玩家行动去对选项表 | `_segment_tension` 不再包含 choices，只保留 dramatic_goal / pressure / 场景文本 |
| 拒绝时改写玩家的行动 | 玩家说"把信交给周伯"，否决后的正文写成"林岑没有立刻做出选择" —— 行动被无视 | `RULING_RULES` 明确：正文必须回应行动本身；不可行时写"尝试了什么、为何行不通"，绝不能改写成"他在犹豫" |
| 裁量偏严 | "老师要求亲自送达"这种软约束被当成拒绝理由 | `RULING_RULES` 明确裁量倾向：犹豫、两难、他人看法是**后果与代价**，不是拒绝理由；只有与既定事实、物理常识、角色能力冲突才拒绝 |

修复后的闭环实测（同一个玩家行动序列）：

1. **行动**："我把信交给周伯，托他带过河"（两个预设选项之外的第三条路）
   - 成立。裁量理由明确说"这属于角色在紧迫局势下的自主抉择"；
   - 后果由模型自行命名并结构化：`letter_location=zhou_bo`、`trust=1`、
     **`relationship_values.ah_he=-1`** —— 帮阿禾被搁置的代价，以关系值的形式进入了状态；
   - 正文里阿禾"眼中的焦急并未消散，但多了一份对林岑选择的审视" —— 代价同时落在了叙事上。
2. **行动**："我摸了摸怀里，检查那封信还在不在"
   - 被拒，理由直接引用快照：**"根据当前状态 letter_location=zhou_bo，信已经交给周伯……
     与既定事实冲突"**；
   - 正文："指尖触到的只有被雨水浸透的衬衫布料，那里空空如也……周伯正站在船舷边，
     一只手按在胸口，那里隔着湿衣隐约透出信纸的轮廓。他意识到自己刚才确实将信交了出去。"

第 2 条是"世界会记住"的直接证明：**模型读了快照，纠正了玩家的记忆，而纠正本身是叙事。**
同时它说明否决信息是有用的 —— 玩家从否决中得知了"信现在在哪"。

一个遗留的界面小问题：CLI 在否决时打印「世界没有接受这个行动」，措辞仍偏系统化，
试玩收集反馈后再定文案。
