# TARI 接手核验与下一步计划 — 2026-09-16

本计划依据交接记录、架构文档、路线图和本次代码核验整理。交接记录中的历史操作建议不等于本轮用户授权；本轮完成接手核验与规划，没有提交、推送或启动真实 LLM 编译。

> 本文是初次接手记录。后续评审及用户确认已将重心转向低频共创的连续故事；当前执行顺序以 [产品方向](product-direction.md) 和 [新版路线图](technical-roadmap.md) 为准。Story 已开始修复版本提交、效果校验及写手上下文；下文保留当时的任务明细，不代表这些问题仍全部未处理。

## 1. 已核验的基线

- 当前分支 `main`，HEAD 与本地 `origin/main` 均为 `33bbec5`；本次未 fetch，不能据此断言远端实时状态。
- 接手时恰有交接记录列出的四项未提交改动：`story/bundle.py`、`narrative/storage.py`、`tests/test_story_reliability.py`、`docs/HANDOFF-2026-09-16.md`。
- 两项实现分别限制 StoryBundle 支持的 schema 版本、将 Story 提交时的 SQLite 完整性冲突转换成 ValueError；异常仍经过共享连接管理器整体回滚。
- 新增 Story reliability 文件有 15 个确定性用例；跨 session 的 request-id 冲突另有既存 runtime 测试覆盖。
- Campaign 已有失败回滚、审计拒绝、失败重试骰点一致性、request-id 重放、快照重建测试。因此“Campaign M2 完全未动”应理解为尚未完成该里程碑，而不是没有可靠性基础。

本次本地验证：

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 通过；2 个真实 LLM 用例默认跳过 |
| `.venv/bin/ruff check .` | 通过 |
| `.venv/bin/mypy src` | 32 个源文件，0 error |
| `.venv/bin/python -m compileall -q src tests` | 通过 |
| `git diff --check` | 通过 |
| 新增 reliability 测试的 Ruff 格式检查 | 通过 |

真实 endpoint 的可用性、全量 Odyssey 编译结果与语义抽查仅为上一轮的历史证据，本次没有重新验证。

## 2. 本次发现与契约差异

### Campaign 的 request-id 缺少所属 campaign 校验

`storage.EventStore.load_turn_result()` 只按 request-id 取结果；`runtime.TurnOrchestrator.process_turn()` 命中后直接返回当前 campaign 的快照和缓存结果，没有检查两者属于同一 campaign。

本次用临时 SQLite 和 FakeAgentSuite 复现：

1. 创建 `station-zero` 与 `second-campaign`。
2. 前者以 `shared-request` 完成一回合。
3. 后者以同一 request-id 发起不同输入。
4. 返回状态属于 `second-campaign`、仍为第 0 回合；返回结果却属于 `station-zero`。

这是已复现的跨 campaign 结果串用，应在新增能力前修复。复现没有调用网络，也没有修改项目数据库。

### Story request-id 的文档表述不一致

当前实现为同一 Story 数据库内的全局唯一 key；同 session/branch 重放返回首次结果，即使 payload 不同；跨 session 或 branch 复用会被拒绝。路线图 §4.2 的 “branch-local request-id” 容易让人理解为不同分支可复用同一 key。

建议本阶段保持现有行为并修正文档。若未来需要按分支独立命名空间，应作为有数据库迁移和兼容测试的独立变更。

### 故障注入证据尚不足以宣告 M2 完成

现有“进程中断”用例是丢弃尚未提交的内存事务，再重新打开 store；它证明未提交缓冲不会落库，但尚未覆盖独立进程在数据库写入中被终止。编译器损坏缓存用例也主要使用能生成相同内容的 Fake，不能直接证明再生成内容变化时所有下游缓存都会正确失效。

此外，路线图与交接记录的 Odyssey 数量来自不同运行；后续报告须记录运行标识与输入摘要，不能把实体或弧数量当成固定验收阈值。

## 3. 执行顺序与完成标准

以下为待执行任务，不能视为已实现。

| 顺序 | 任务 | 交付与验收条件 |
| --- | --- | --- |
| P0 / A | 修复 Campaign request-id 所属校验 | 同 campaign 重放不调用 Agent、不追加事件；跨 campaign 复用明确拒绝；两侧快照与历史不变；覆盖 runtime 与 HTTP 路径 |
| P0 / B | 收口已接手的 Story 改动与契约文档 | 保留现有 15 项回归；明确 schema v1/未知版本拒载策略、全局 request-id 与 payload 重放行为；核对 HTTP 冲突映射；形成可独立审阅的改动批次 |
| P1 / C | 固化常规 CI 门禁 | PR/push 自动运行 pytest、Ruff、mypy、compileall；测试不依赖本地 `.env`、真实密钥或本机素材；真实 LLM E2E 保持显式 opt-in |
| P1 / D | Story M2 故障注入报告 | 建立固定 seed 的 100 次回合/尝试场景及逐编译阶段故障矩阵；产出可重复生成的摘要和失败明细，满足下述不变量 |
| P1 / E | 编译输出三层质量评估 | 分别给出结构校验、evidence 校验、语义抽样结果；以 fixture 验证评估器；真实编译结果附独立运行标识 |
| P1 / F | Campaign 显式协议与生命周期 | 拆分 GMPlan/GMResolution，禁止 resolution 改写 plan 的 check/stakes；定义 provisional/committed/diagnostic；明确 started/aborted/committed 事件和恢复规则；保留兼容入口 |
| P1 / G | Campaign 重试策略与 100 回合报告 | 固定 timeout/schema/audit 的重试次数、退避和耗尽行为；只重试允许的阶段；生成故障注入报告并证明骰点、版本和提交一致性 |
| P2 / H | M3 服务与 Web 适配层收敛 | 先拆 routes/services、保持 CLI/API/import 兼容，再交付 Story 最小可玩页面；清理 settings provider 硬编码 |

建议先完成 A，再处理 B/C，随后按 D/E、F/G 收完两侧 M2。CI 尽早固定已有门禁，降低后续协议改造的回归风险。已有未提交改动无需等待 commit 才能继续本地实现；提交与推送作为单独操作处理。

### D：Story 故障注入的具体范围

- 回合路径：成功、重复请求、不同 payload 重放、跨 session/branch 冲突、author 异常与无效提案、fork 后父分支推进、重开数据库恢复。
- 编译路径：source plan、chapter cards、arcs、world knowledge、structures、bundle/export；逐阶段注入异常、损坏和缓存失效，区分应该重算与应该复用的产物。
- 补一个受控子进程中断场景，验证事件写入、快照和幂等结果之间不出现部分提交；不得影响实际运行数据。
- 用会改变再生成结果的 Fake 检验下游依赖失效；用计数器证明暖缓存及已完成请求重放为零 provider 调用。
- 报告记录 seed、尝试数、成功提交数、注入点、预期/实际恢复结果、调用次数、版本和事件一致性；100 次不能全部由同一路径重复凑数。

### E：质量评估的边界

- 结构：正式 loader、schema、阶段完成情况、引用可解析、可创建会话并运行一回合。
- Evidence：源 SHA 与章节边界正确、source refs 可解析且位于有效源范围、覆盖缺口单独列出。引用存在不等于内容受到原文支持。
- 语义：固定抽样规则核对事实、关系和剧情与原文是否一致，标注原文支持、推断或矛盾，保留出处及判断理由；不以模型自评替代证据。
- 结构与引用不变量作为硬门禁；语义指标和阈值先用标注样本校准，不能凭一次模型输出的数量决定合格。

### F/G：Campaign 的兼容与恢复要求

- GMPlan 与 GMResolution 的 typed contract 独立测试，修改模型、runtime、Fake/PydanticAI adapter 时同步覆盖现有 CLI/Web/streaming 调用者。
- 先定义 `turn_started` 是否仅为 diagnostic，再确定落库方式；不能因新增生命周期事件让未完成回合成为权威历史。
- 重试预算必须包含 provider/SDK 内部重试，防止叠加造成不可控调用次数。
- 100 回合报告覆盖 plan、resolve、actor、audit、commit 前后故障；已提交请求重放零调用，失败重试保持确定性骰流，无重复权威骰点、patch 或事件提交。

## 4. 阶段出口与后续边界

M2 完成需要两个 context 各自的故障注入报告、独立协议测试和全量质量门禁；现有短程测试全绿只是基线。每个实现批次先运行相关回归，结束时执行路线图规定的全量检查。

Story UI 的最小范围建议为资源选择、创建会话、choice/freeform、历史、fork 与重载。先完成 Story M2 gate 和对应服务边界，再进入该任务。

公开访问仍以鉴权、ownership、限流、请求大小、超时/取消和秘密过滤为前置门禁；若部署目标变为公网，安全任务须提前。当前计划不扩展 embedding、多 Actor、完整 reroll/rollback 或 SillyTavern/public adapter。

相关依据：[交接记录](HANDOFF-2026-09-16.md)、[架构](architecture.md)、[技术路线图](technical-roadmap.md)、[安全边界](security.md)。
