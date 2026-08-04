from __future__ import annotations

DEFAULT_LOCALE = "en"

# Register new languages here. The key is the locale code, the value is the
# human-readable language name used in agent prompts.
SUPPORTED_LOCALES: dict[str, str] = {
    "en": "English",
    "zh": "中文",
}

LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "en": (
        "Write all narration, dialogue, reasoning summaries, stakes, and patch reasons in English."
    ),
    "zh": (
        "Write all narration, dialogue, reasoning summaries, stakes, and patch reasons in "
        "Simplified Chinese (简体中文)."
    ),
}

UI_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "campaign_created": "Campaign created: {id}",
        "seed": "Seed: {seed}",
        "spotlight": "Spotlight: {owner}",
        "turn_failed": "Turn failed without advancing state: {error}",
        "turn_title": "turn {n}",
        "roll": "Roll: {a} + {b} = {total} -> {outcome}",
        "gm": "GM",
        "actor": "Actor",
        "progress.gm_planning": "GM is planning…",
        "progress.rolling": "Rolling…",
        "progress.gm_resolving": "GM is resolving…",
        "progress.committing": "Committing state…",
        "progress.actor_turn": "Actor is responding…",
        "progress.auditing": "Auditing…",
        "progress.completed": "Turn complete",
        "outcome.full_success": "full success",
        "outcome.success_with_cost": "success with cost",
        "outcome.failure": "failure",
    },
    "zh": {
        "campaign_created": "战役已创建：{id}",
        "seed": "随机种子：{seed}",
        "spotlight": "当前 Spotlight：{owner}",
        "turn_failed": "回合失败，状态未推进：{error}",
        "turn_title": "第 {n} 回合",
        "roll": "掷骰：{a} + {b} = {total} → {outcome}",
        "gm": "GM",
        "actor": "角色",
        "progress.gm_planning": "GM 正在规划…",
        "progress.rolling": "正在掷骰…",
        "progress.gm_resolving": "GM 正在裁定…",
        "progress.committing": "正在提交状态…",
        "progress.actor_turn": "角色正在回应…",
        "progress.auditing": "语义审计中…",
        "progress.completed": "回合完成",
        "outcome.full_success": "完全成功",
        "outcome.success_with_cost": "成功但付出代价",
        "outcome.failure": "失败",
    },
}


def locale_name(locale: str) -> str:
    return SUPPORTED_LOCALES.get(locale, locale)


def language_instruction(locale: str) -> str:
    return LANGUAGE_INSTRUCTIONS.get(locale, LANGUAGE_INSTRUCTIONS[DEFAULT_LOCALE])


def t(locale: str, key: str, **kwargs) -> str:
    table = UI_STRINGS.get(locale, UI_STRINGS[DEFAULT_LOCALE])
    template = table.get(key) or UI_STRINGS[DEFAULT_LOCALE].get(key) or key
    return template.format(**kwargs) if kwargs else template
