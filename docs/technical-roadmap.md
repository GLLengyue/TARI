# TARI 技术路线图 | Technical Roadmap

> 本路线图按架构边界和退出条件推进，不按“增加模型调用次数”推进。版本号表示能力里程碑，不承诺日期。
>
> TARI is planned by architectural boundaries and exit gates, not by adding model calls. Version labels are capability milestones, not calendar promises.

## 1. 当前基线 | Current baseline

| 项目 | 状态 |
| --- | --- |
| 版本 | `v0.1` MVP |
| 传统 Campaign context | 已有可审计 TRPG loop、2d6、Spotlight、GM/Actor/Auditor、事件/快照/恢复 |
| Story context | 已有 source import、可恢复语义编译、Story Bundle、选择/自由行动/分支 runtime |
| 共享内核 | `persistence.SQLiteStore` 和 `llm.OpenAICompatibleClient` 已落地 |
| 客户端 | CLI 和本机 Web vertical slice；Story API 仅面向程序客户端，Web 控制台尚无 Story 页面；SillyTavern adapter 仍在路线图 |
| 本轮验收 | Odyssey：24 章、6 弧、311 entities、352 facts、229 relationships；另在仓库外以 clean-room 方式一次跑通原文→Bundle（24/24 章、6 弧、`failures={}`、source SHA 一致）。全量 pytest/Ruff/mypy 通过；完整 Odyssey 编译因耗时且依赖真实 LLM 端点，以 `TARI_E2E_COMPILE=1` 门控用例固化于 `tests/test_story_compile_e2e.py`（默认 skip，不进入常规 pytest） |

架构基线见 [architecture.md](architecture.md)。核心决策是：**Campaign 和 Story 是两个 bounded context；共享 persistence/llm 基础设施，但不共享业务状态、事件语义或运行时继承关系。**

## 2. 不可违反的架构约束 | Non-negotiable constraints

1. **Runtime authority**：LLM 只能返回 typed proposal 或 prose；确定性 runtime 拥有状态、骰点、权限、分支和提交。
2. **Context isolation**：`CampaignState`、`StorySessionState`、`StoryBundle` 不互相转换，不创建跨领域 God object。
3. **Shared kernel stays small**：共享层只放 SQLite 生命周期、LLM endpoint/config/JSON transport 等无领域语义能力。
4. **Append-only history**：事件只追加；编辑旧 transcript 必须创建分支或显式 runtime 操作。
5. **Atomic commit**：一个已接受的回合的事件、快照和幂等结果必须原子提交；失败不能留下半个权威回合。
6. **Adapters stay thin**：CLI/Web 不直接操作 SQLite 表，不复制领域校验，不让 HTTP schema 反向成为 domain schema。
7. **Evidence boundary**：编译器产物必须保留 source refs；模型推断不能绕过 Bundle 校验成为 canon。

## 3. 已完成里程碑 | Completed milestones

### M0：Story Mode vertical slice — 已完成

交付内容：

- `story-import`：UTF-8 TXT/Markdown 的确定性 source-preserving scaffold；
- `story-compile`：source plan、chapter cards、rolling arcs、world knowledge、volume/novel structures、Bundle 和审计中间产物；
- manifest/checkpoint/source SHA/content SHA/settings fingerprint/atomic writes；
- local llama-server 的 Qwen thinking 关闭、JSON mode、串行 world batches、失败 checkpoint 和续跑；
- `NarrativeOrchestrator`：choice、freeform、continue、窄状态 patch、事实揭示、terminal beat；
- `StoryStore`：事件、快照、分支、request-id 幂等；
- CLI 和初版 `/api/story/...` HTTP surface；
- Odyssey 离线产物验收和 compiler/runtime/API 回归测试。

验收门禁：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
git diff --check
```

### M1：架构收敛 — 当前已完成

- `persistence.SQLiteStore` 提供共享 SQLite 生命周期，Campaign/Story 各自定义 schema；
- `llm.LLMSettings`、`OpenAICompatibleClient`、`extract_json_object` 成为跨上下文 provider 基础设施；
- Story compiler 不再依赖 `narrative.providers`；
- StoryStore 不再继承 EventStore；
- 删除只服务 Odyssey 一次运行的 detached launcher、merge probe、status 和 bundle verifier；
- 清零全仓 Ruff 基线问题；
- 架构文档和本路线图与实际实现对齐。

## 4. 下一阶段：可靠性合同化 | M2 Reliability contracts

**目标：** 不增加业务表面，先证明两个 context 都能在 provider 失败、进程中断和重复请求下保持权威历史正确。

### 4.1 Campaign context

1. 将 `GMPlan` 和 `GMResolution` 变成显式协议，runtime 拒绝 resolution 修改 plan 的 check/stakes；
2. 明确 provisional、committed、diagnostic 三类数据；
3. 为 `TurnTransaction` 增加 `turn_started`/`turn_aborted`/`turn_committed` 语义和恢复测试；
4. provider timeout、schema error、audit rejection 的重试预算和退避策略固定化；
5. 100 回合 Fake Agent 故障注入：无重复骰点、重复提交、半完成回合或 version divergence。

### 4.2 Story context

1. 为 compiler 增加每个阶段的错误恢复和损坏 checkpoint 测试；
2. 为 StoryStore 增加进程中断、重复 commit 和 request-id 冲突测试；
3. 明确 branch fork point、ancestor event projection 和 branch-local request-id 语义；
4. 对 `StoryBundle` 做 schema version/migration policy，不在 runtime 中静默接受未知 schema；
5. 将编译器输出质量分成结构完整性、source evidence 完整性和语义抽样评估，不把 LLM 文本“看起来合理”当作验收标准。

### M2 退出条件

- 两个 context 各有 100 回合或等价阶段故障注入报告；
- 重复 request 不重复调用 provider、不重复应用 patch、不重复提交事件；
- provider/进程失败后只能从最后一个 committed state 恢复；
- Story compiler 任意一个阶段失败后可续跑，已完成阶段不被无故重算；
- Campaign 和 Story 的公开协议都能在不读取对方 domain 模块的情况下测试。

## 5. 应用服务和适配器收敛 | M3 Application services and adapters

**目标：** 把当前能工作的 CLI/Web vertical slice 变成长期可维护的 adapter 层，不改变 domain/runtime 契约。

### 5.1 拆分 Web composition root

将 `web/app.py` 拆为：

```text
web/
  app.py              # FastAPI factory, middleware, exception policy
  campaign_routes.py  # legacy Campaign HTTP adapter
  story_routes.py     # Story HTTP adapter
  resource_routes.py  # ResourceLibrary adapter
  services.py         # request -> application workflow wiring
```

路由只做输入验证、错误映射和 response serialization；状态变更必须调用 context application service。

### 5.2 稳定 CLI/workflow 边界

- 保留现有命令作为兼容入口；
- 将 `narrative.workflow` 的 compile/session façade 迁移到明确的 application service 模块，旧 import 路径保留兼容转发；
- CLI 不承担 provider 重试、patch 校验或存储 schema 逻辑；
- 为每个 CLI 命令增加最小 smoke test，而不是用端到端脚本代替产品接口。

### 5.3 安全门禁

在任何公开监听或客户端 adapter 之前必须具备：

- authentication/authorization；
- request size、rate limit、timeout 和 cancellation；
- secrets 不进日志、event payload 或 debug response；
- Story author-only facts、Campaign hidden facts 和 CoT 不进入公开 transcript；
- branch/session/resource 的 ownership 检查。

## 6. Story Mode 产品化 | M4 Story runtime

**前置条件：** M2 的 Story reliability gate 通过；不以新增前端替代 runtime 验收。

### 6.1 场景与时间线

- 在 `StorySessionState` 内增加显式 scene/anchor/timeline 概念；
- choice 和 freeform 统一为 `DecisionInput`，但保留“自由行动默认不推进 beat”的权威规则；
- branch tree、fork、replay/projection 形成可测试的 timeline service；
- `CanonPolicy`（strict/guided/sandbox）影响可用 decision 和事实投影，而不是由模型自由解释；
- 支持从 Story Bundle 的 source evidence 定位当前 beat/arc 的上下文。

### 6.2 互动写手协议

固定单次回合合同：

```text
Input:
  canon policy / scene / session / player identity / player knowledge / decision

Runtime resolves:
  target beat / allowed effects / allowed reveals / next choices / terminal state

Author returns:
  narration only (plus optional diagnostic metadata)

Runtime commits:
  validated state patch + event set + snapshot + idempotent result
```

任何扩展字段都必须说明权威方和验证方；不能因为模型能生成 JSON 就把 beat、facts 或 timeline ownership 交给模型。

### M4 退出条件

- Story Mode 可以完成一篇多场景短篇，choice/freeform/branch/reload 均有回归测试；
- 分支不改变 parent snapshot，ancestor event projection 可重复；
- author 输出 malformed、越权 patch、越权 reveal、错误 beat 时，状态不推进；
- 公开 HTTP response 不泄漏 author-only/private state；
- 有一个专用 Story UI 或成熟 API client，再考虑 SillyTavern adapter。

## 7. Campaign 扩展 | M5 Campaign multi-actor

**前置条件：** M2 Campaign reliability gate 通过，且不再把单场景一 Actor 的假设藏在 runtime 中。

- 多 Actor 实例化：每个 Actor 有独立 `ActorView`、model profile、knowledge projection；
- Knowledge graph：truth、belief、player knowledge、source、acquired turn、visibility 分离；
- Spotlight scheduler：Player/GM/Actor/Shared/Interrupt，连续输出和归还策略由 runtime 控制；
- GM 只能提出 actor dispatch，不能由 Actor 自行获得发言权；
- 至少 3 个 Actor、30 回合、无私有知识串线、无未获 spotlight 角色擅自发言。

这部分属于 Campaign context，不应通过修改 Story Bundle 或 Narrative runtime 来实现。

## 8. Provider、流式和外部客户端 | M6 Providers and clients

在 M2/M3 后再做：

1. 在 `llm` 共享内核上增加明确的 provider capability（JSON mode、thinking control、streaming、tool calling）；
2. Campaign agent adapter 和 Story author adapter 分别声明所需 capability；
3. 流式输出只作为 UX/diagnostic channel，公开正文仍由 runtime commit 产生；
4. 实现 OpenAI-compatible `/v1/chat/completions` facade；
5. 再实现 SillyTavern adapter，不让 SillyTavern transcript 成为权威状态。

云端 GM、本地 Actor、KoboldCpp 等属于 provider deployment 选择，不能反向改变 domain 权限模型。

## 9. 延后项和删除策略 | Deferred and deletion policy

### 保留为 TODO，不现在实现

- embedding/vector memory；先完成结构化 fact/event/beat retrieval；
- ruleset plugin marketplace；先稳定 `pbta-minimal` 和 domain port；
- 完整 regenerate/reroll/rollback；先完成 branch/fork/replay 语义；
- Story 专用浏览器页面；先稳定 HTTP/application service contract；
- OpenAI/SillyTavern public adapter；先完成 auth/security/streaming contract。

### 已删除

- Odyssey detached launcher、merge probe、status script、bundle verifier：一次性实验运维代码，不属于产品架构。

### 允许删除的条件

任何旧代码只有在以下条件同时满足时才能删除：

1. 已有替代 application/domain API；
2. 旧 CLI/HTTP/import path 有迁移或兼容测试；
3. Story compiler/runtime/Campaign runtime 回归均通过；
4. 文档、examples 和配置不再引用旧路径；
5. 删除原因写入 changelog/commit，不靠隐式清理。

## 10. 统一质量门禁 | Quality gates

每个跨边界变更必须运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m compileall -q src tests
git diff --check
```

新增文件必须通过 `ruff format`；已有 14 个历史文件（早于 M0/M1）仍未格式化，本轮不扩大这份名单，也不在功能提交里顺手重排无关文件。`mypy src` 在 dev extra 装上 `types-PyYAML` 后应为 0 error。

涉及 Story compiler 时，还要验证：

- `manifest.status` 与阶段 checkpoint；
- `bundle.yaml` 可由正式 loader 加载；
- source SHA/evidence/实体事实关系引用存在；
- 至少一次从 cache 续跑；
- 不发送真实 token、密钥或用户数据到未明确授权的外部 endpoint。

路线图的每个阶段都必须同时给出功能结果、失败行为和回归证据；没有退出条件的功能先不进入实现。
