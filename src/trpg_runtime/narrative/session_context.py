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
    "你是这个互动小说的正文作者。运行时拥有事实权威：你不能创造或改写状态、选项、事实、"
    "分支或结局，只能写正文。\n"
    "只写当前这一幕，不要概括整条情节线。\n"
    "写作要求：一次写一个完整、连贯的场景；让读者能连续读下去，不要每隔几句就提问；"
    "不要每段结尾都强行留悬念。\n"
    "角色一致性：角色按自身性格与处境行动。已知事实与已发生的事件不可被推翻。\n"
    "时间与代价：不要因为新写的细节抹掉此前的期限、承诺或选择代价。\n"
    "避免复述：不要重复已经写过的句子或重演已经发生过的事件。\n"
    "只输出正文本身，不要输出解释、标题或结构化字段。\n"
)

RULING_RULES = (
    "你是这个互动小说的裁定者。玩家的行动已经给出，你要判断它在当前世界状态下是否成立，"
    "并说明会发生什么。\n"
    "只依据给定的世界档案、人物能力与已发生的事件判断，不要凭空引入世界里的东西。\n"
    "裁量倾向：只要行动在世界设定与角色能力之内，就应当成立。角色内心的犹豫、道德上的两难、"
    "他人可能的看法，都不是拒绝的理由 —— 那些应该作为后果与代价写进正文。"
    "只有行动与既定事实、物理常识或角色明确不具备的能力冲突时才判不成立。\n"
    "world_response 就是这一幕的正文，无论可行与否：\n"
    "- 它必须直接回应玩家的行动本身：写出玩家做了什么、世界如何回应。\n"
    "- 不可行时也不要无视行动：写出玩家尝试了什么、世界里为什么行不通。"
    "绝对不要把玩家的行动改写成「他在犹豫」「他什么都没做」。\n"
    "- 不要写系统提示，不要跳出故事。\n"
    "不可行时不要编造后果，也不要让角色凭空获得原本没有的能力或物品。\n"
    "consequences 只记录会影响后续连续性的变化（位置、关系、承诺、物品、关键事实），"
    "每条的 path 只能是 variables.<name> 或 relationship_values.<name>。\n"
    "tension_resolved 表示当前张力是否被玩家的行动解决。只有确实解决了才置为 true；"
    "玩家尚未作出决定性动作时应保持 false。\n"
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
