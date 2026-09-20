"""Append-only prompt assembly for Story Mode.

The prompt is built as three fixed segments so the provider can keep hitting its
prefix cache across turns:

    1. static prefix  -- rules, world profile, session identity; written once
    2. growing tail   -- the event log projected in order; only appended to
    3. this turn      -- the player's new action

Anything that changes per turn must land at the end. Nothing in (1) or in the
already-projected part of (2) may be rewritten, re-sorted, or windowed, because
a single byte of change invalidates the cache from that point on.

This module is deliberately free of I/O and of the model client so the prefix
stability is testable on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..story.bundle import FactVisibility, StoryBundle
from .domain import StorySessionState

#: Fraction of the model window at which the caller should compact the tail.
COMPACTION_THRESHOLD = 0.7

#: Rough characters-per-token ratio used only for the compaction estimate.
_CHARS_PER_TOKEN = 1.6

#: Bookkeeping variables the runtime writes; the history already carries them.
_REDUNDANT_SNAPSHOT_KEYS = frozenset({"last_input", "last_choice"})

AUTHOR_RULES = (
    "# 正文作者手册\n"
    "你是这个互动小说的正文作者。运行时拥有事实权威：状态、选项、事实、分支与结局都归它管，"
    "你只写正文。\n"
    "\n"
    "## 核心立场\n"
    "- **做主角的粉丝。** 世界如实呈现、真实有力，但你是讲故事的人，不是把关人。\n"
    "- **描绘处境，不替玩家决定。** 玩家说角色做了什么，你就写它如何展开；"
    "角色内心往哪里去是玩家的领地，不要擅自替他动摇、改主意或无动于衷。\n"
    "\n"
    "## 笔法\n"
    "- 一次写一个完整场景，让读者能连续读下去，不要每隔几句就停下来提问。\n"
    "- 用具体的感官细节落笔，不写抽象的总结。\n"
    "- 角色按自身性格与处境行动；已发生的事不可推翻，期限、承诺与代价不可被新细节悄悄抹掉。\n"
    "- 复述是敌人：不重复写过的句子，不重演已经发生过的事件，不把别的场景整段搬过来。\n"
    "- 只输出正文本身：没有标题，没有解释，没有结构化字段。正文使用会话头注明的语言。\n"
)

RULING_RULES = (
    "# 主持人手册（裁定者）\n"
    "你是这个互动小说的主持人：裁定玩家的自由行动，并以世界的身份回应。\n"
    "\n"
    "## 核心立场\n"
    "- **做主角的粉丝。** 如实呈现世界 —— 它确实对他不利 —— 但你不要成为他的敌人；"
    "他的敌人已经够多了。对他的选择保持兴趣，为他的结果投入感情。\n"
    "- **不要堵路。** 你的工作不是宣布「你不能这样做」。玩家的行动几乎总有回答，"
    "多数时候是「能，但是……」：能做，但有代价，或者路更难。"
    "即使那条路漫长危险，也要指出它在哪里。\n"
    "- **玩，是为了发现会发生什么。** 不要预设玩家该走哪条路，"
    "更不要拿任何预设的走向去给他的行动打分。他自己找到的路，就是这一幕的路。\n"
    "\n"
    "## 如何裁定\n"
    "- **先读状态，再裁定。** 裁定任何行动之前，先核对最后一条状态快照："
    "行动若与已记录的状态冲突 —— 手上没有的物品、已经离开的地方、已经交出去的东西 —— "
    "判不成立，并在正文里如实呈现。不要顺着玩家的说法改写既成事实；"
    "玩家记错了，恰恰是世界回应他的机会。\n"
    "- **一切从虚构中流淌。** 只依据世界档案、人物能力与已发生的事件判断；"
    "不凭空引入世界里的东西，也不凭空排除世界里的东西。\n"
    "- **危险不等于不可能。** 默认行动是有风险的：风险化为代价与新的局面，而不是化为禁止。"
    "犹豫、两难、他人的不满，都是后果与代价，写进正文，而不是当作拒绝的理由。\n"
    "- **失败也要向前。** 判定不成立时，世界也必须前进一步："
    "写出他尝试了什么、为什么行不通、以及这段时间世界又动了什么。"
    "绝不把他的行动改写成「他在犹豫」「他什么都没做」。\n"
    "- **先软后硬。** 打击之前先给世界内的征兆：他摸向腰间 —— 那里只有一柄小刀；"
    "周伯已经开始解缆绳。让玩家从世界里读到警告，而不是从系统里读到拒绝。\n"
    "- **对角色说话。** 正文必须回应玩家的行动本身：他做了什么、世界如何回应。"
    "不要写系统提示，不要跳出故事。\n"
    "- **诚实。** 世界的回应基于给定的真实状态；不顺着玩家的期待编造，"
    "也不让角色凭空获得他原本没有的能力或物品。\n"
    "- **世界不等待。** 他犹豫的时候，时钟在走，别人在动。\n"
    "\n"
    "## 技术契约\n"
    "consequences 只记录影响后续连续性的变化（位置、关系、承诺、物品、关键事实），"
    "path 只能是 variables.<name> 或 relationship_values.<name>。\n"
    "tension_resolved 只在玩家的行动确实解决了当前张力时为 true。\n"
    '只返回 JSON：{"feasible":true,"reason":"...","world_response":"...",'
    '"consequences":[{"operation":"set","path":"variables.x","new_value":1,"reason":"..."}],'
    '"tension_resolved":false,"resolution":""}\n'
)


def _derive_world_profile(bundle: StoryBundle) -> str:
    """Fallback profile when the compiler did not emit one.

    Uses only stable bundle content, in bundle order, so the result is identical
    on every call for the same bundle.
    """
    sections: list[str] = [f"# 世界：{bundle.title}"]

    style = bundle.style_profile
    style_bits = [f"语言：{style.language}", f"视角：{style.point_of_view}", f"语气：{style.tone}"]
    if style.constraints:
        style_bits.append("约束：" + "；".join(style.constraints))
    sections.append("写作基调：" + "，".join(style_bits))

    characters = [item for item in bundle.entities if item.kind == "character"]
    if characters:
        lines = ["## 人物"]
        for entity in characters[:60]:
            detail = entity.description.strip()
            lines.append(f"- {entity.name}：{detail}" if detail else f"- {entity.name}")
        sections.append("\n".join(lines))

    others = [item for item in bundle.entities if item.kind != "character"]
    if others:
        lines = ["## 地点与物品"]
        for entity in others[:60]:
            detail = entity.description.strip()
            lines.append(f"- {entity.name}：{detail}" if detail else f"- {entity.name}")
        sections.append("\n".join(lines))

    facts = [item for item in bundle.canon_facts if item.visibility != FactVisibility.AUTHOR_ONLY]
    if facts:
        lines = ["## 已知事实"]
        lines.extend(f"- {fact.content}" for fact in facts[:120])
        sections.append("\n".join(lines))

    return "\n\n".join(section for section in sections if section.strip())


def build_static_prefix(bundle: StoryBundle, state: StorySessionState) -> list[dict[str, str]]:
    """Return the immutable head of every request in this session.

    Deterministic given (bundle, identity, locale): the same session must produce
    byte-identical output on every turn, otherwise the provider cache never hits.
    """
    identity = state.player_identity
    profile = bundle.world_profile.strip() or _derive_world_profile(bundle)

    header_lines = [
        f"# 会话\n故事：{bundle.title}\n语言：{state.locale}",
        f"玩家扮演：{identity.display_name}（{identity.identity_type}）",
    ]
    if identity.host_character:
        header_lines.append(f"所附角色：{identity.host_character}")
    if identity.persona.strip():
        header_lines.append(f"扮演设定：{identity.persona.strip()}")

    # Only the frozen copy may enter the prefix: live variables change every turn.
    initial_variables = state.initial_variables
    if initial_variables:
        rendered = "；".join(f"{key}={value}" for key, value in sorted(initial_variables.items()))
        header_lines.append(f"起始状态：{rendered}")

    if bundle.opening.strip():
        header_lines.append(f"开场：{bundle.opening.strip()}")

    # Exactly one system message: several chat templates (llama.cpp's Qwen3.8
    # among them) reject any system message that is not the very first one.
    return [
        {
            "role": "system",
            "content": "\n\n".join([RULING_RULES, AUTHOR_RULES, profile, "\n".join(header_lines)]),
        }
    ]


def render_state_snapshot(payload: dict[str, Any]) -> str:
    """Render one state snapshot, deterministically.

    The snapshot is produced by the runtime from authoritative state -- never
    recalled by a model. A model-written snapshot could drift from the truth,
    and since the snapshot is the model's only view of state, nothing
    downstream would catch the drift.

    Bookkeeping-only keys are dropped: the player's input and chosen option are
    already in the history, so repeating them only costs tokens.
    """
    chunks: list[str] = []
    variables = {
        key: value
        for key, value in (payload.get("variables") or {}).items()
        if key not in _REDUNDANT_SNAPSHOT_KEYS
    }
    if variables:
        chunks.append("；".join(f"{key}={value}" for key, value in sorted(variables.items())))
    relationships = payload.get("relationship_values") or {}
    if relationships:
        rendered = "；".join(f"{key}={value}" for key, value in sorted(relationships.items()))
        chunks.append("关系 " + rendered)
    if not chunks:
        return ""
    return "（旁白）当前状态：" + "。".join(chunks)


def project_events(events: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Project the event log into messages, in order.

    Only user and assistant roles are emitted: several chat templates reject a
    system message that is not the very first one, so in-world bookkeeping
    arrives as bracketed narration instead.

    The state delta is deliberately not projected -- the prose already narrates
    what changed, and repeating it would cost tokens without adding information.

    Prefix stability: the projection of ``events[:n]`` is always a prefix of the
    projection of ``events[:n+1]``. No windowing, no re-ordering, no de-duping.
    """
    messages: list[dict[str, str]] = []
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "story_player_input_received":
            if payload.get("automatic"):
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                messages.append({"role": "user", "content": text})
        elif event_type == "story_narrative_emitted":
            text = str(payload.get("text") or "").strip()
            if text:
                messages.append({"role": "assistant", "content": text})
        elif event_type == "story_state_snapshot":
            snapshot = render_state_snapshot(payload)
            if snapshot:
                messages.append({"role": "user", "content": snapshot})
        elif event_type == "story_segment_opened":
            tension = str(payload.get("tension") or "").strip()
            stakes = str(payload.get("stakes") or "").strip()
            block = f"（旁白）玩家现在可以自由行动。当前张力：{tension}"
            if stakes:
                block += f"\n代价：{stakes}"
            messages.append({"role": "user", "content": block})
        elif event_type == "story_segment_resolved":
            resolution = str(payload.get("resolution") or "").strip()
            messages.append(
                {"role": "user", "content": f"（旁白）自由段结束。张力如何解决：{resolution}"}
            )
    return messages


def build_messages(
    prefix: Sequence[dict[str, str]],
    events: Sequence[dict[str, Any]],
    turn_messages: Sequence[dict[str, str]] = (),
) -> list[dict[str, str]]:
    """Assemble the request: static prefix, projected history, then this turn."""
    return [*prefix, *project_events(events), *turn_messages]


def estimate_tokens(messages: Sequence[dict[str, str]]) -> int:
    """Rough size estimate, used only to decide when to compact."""
    total = sum(len(message.get("content") or "") for message in messages)
    return int(total / _CHARS_PER_TOKEN)


def should_compact(messages: Sequence[dict[str, str]], window: int) -> bool:
    if window <= 0:
        return False
    return estimate_tokens(messages) >= int(window * COMPACTION_THRESHOLD)


def prefix_fingerprint(prefix: Sequence[dict[str, str]]) -> str:
    """Stable identity of a static prefix, for cache-hit assertions in tests."""
    import hashlib

    digest = hashlib.sha256()
    for message in prefix:
        digest.update(message.get("role", "").encode("utf-8"))
        digest.update(b"\x00")
        digest.update((message.get("content") or "").encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


__all__ = [
    "AUTHOR_RULES",
    "COMPACTION_THRESHOLD",
    "RULING_RULES",
    "build_messages",
    "build_static_prefix",
    "estimate_tokens",
    "prefix_fingerprint",
    "project_events",
    "render_state_snapshot",
    "should_compact",
]
