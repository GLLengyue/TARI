from __future__ import annotations

from typing import Any

from .domain import CampaignState, KnowledgeItem

_PUBLIC_MARKER = "tari:public"
_HIDDEN_MARKER = "tari:hidden"
_KNOWLEDGE_PREFIX = "tari:know:"


def _entry(uid: int, content: str, marker: str, constant: bool = True) -> dict[str, Any]:
    return {
        "uid": uid,
        "key": [],
        "keysecondary": [],
        "comment": marker,
        "content": content,
        "constant": constant,
        "selective": False,
        "order": 100,
        "position": 0,
        "disable": False,
        "caseSensitive": False,
        "matchWholeWords": False,
        "scanDepth": 4,
        "sticky": False,
        "cooldown": 0,
        "delay": 0,
    }


def world_info_from_state(state: CampaignState, book_name: str | None = None) -> dict[str, Any]:
    """Export campaign facts and actor knowledge as a SillyTavern world-info JSON.

    TARI semantics are preserved through ``comment`` markers:
    ``tari:public`` / ``tari:hidden`` / ``tari:know:<actor_id>``. The markers are
    inert in SillyTavern (comments are not activation keywords).
    """
    entries: dict[str, dict[str, Any]] = {}
    uid = 0
    for fact in state.scene.public_facts:
        entries[str(uid)] = _entry(uid, fact, _PUBLIC_MARKER)
        uid += 1
    for fact in state.scene.hidden_facts:
        entries[str(uid)] = _entry(uid, fact, _HIDDEN_MARKER)
        uid += 1
    for actor_id, actor in state.actors.items():
        for item in actor.knowledge:
            entries[str(uid)] = _entry(
                uid,
                item.content,
                f"{_KNOWLEDGE_PREFIX}{actor_id}",
                constant=item.confidence >= 1.0,
            )
            uid += 1
    return {
        "name": book_name or f"{state.title} (TARI export)",
        "description": "Exported from the TARI runtime; comments carry TARI markers.",
        "scan_depth": 4,
        "entries": entries,
    }


def normalize_world_book(book: dict[str, Any]) -> dict[str, Any]:
    """Normalize a SillyTavern world-info book for TARI import.

    Foreverse cards and some tools ship ``entries`` as a list; TARI's
    ``apply_world_info`` expects a dict keyed by entry uid.  This helper
    converts list entries (keeping ``content``, ``comment``, ``constant``)
    so both shapes import cleanly.
    """
    entries = book.get("entries", {})
    if isinstance(entries, list):
        converted: dict[str, Any] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            uid = str(entry.get("id", index))
            converted[uid] = {
                "uid": uid,
                "key": list(entry.get("keys") or entry.get("key") or []),
                "keysecondary": list(
                    entry.get("secondary_keys") or entry.get("keysecondary") or []
                ),
                "comment": str(entry.get("comment") or ""),
                "content": str(entry.get("content") or ""),
                "constant": bool(entry.get("constant", False)),
                "selective": bool(entry.get("selective", False)),
                "order": entry.get("insertion_order", entry.get("order", 100)),
                "position": entry.get("position", 0),
                "disable": not bool(entry.get("enabled", True)),
                "caseSensitive": bool(entry.get("caseSensitive", False)),
                "matchWholeWords": bool(entry.get("matchWholeWords", False)),
                "scanDepth": entry.get("scanDepth", 4),
                "sticky": bool(entry.get("sticky", False)),
                "cooldown": entry.get("cooldown", 0),
                "delay": entry.get("delay", 0),
            }
        return {**book, "entries": converted}
    return book


def apply_world_info(state: CampaignState, book: dict[str, Any]) -> CampaignState:
    """Import a SillyTavern world info into campaign state.

    Marked entries restore public/hidden facts and actor knowledge; unmarked
    entries become public facts (the GM can reclassify them afterwards).
    """
    public = list(state.scene.public_facts)
    hidden = list(state.scene.hidden_facts)
    actors = {actor_id: actor for actor_id, actor in state.actors.items()}

    entries = book.get("entries", {})
    if not isinstance(entries, dict):
        return state

    for uid, entry in entries.items():
        if not isinstance(entry, dict) or not entry.get("content"):
            continue
        content = str(entry["content"]).strip()
        comment = str(entry.get("comment") or "")
        constant = bool(entry.get("constant"))
        if comment == _HIDDEN_MARKER:
            if content not in hidden:
                hidden.append(content)
        elif comment.startswith(_KNOWLEDGE_PREFIX):
            actor_id = comment[len(_KNOWLEDGE_PREFIX) :]
            actor = actors.get(actor_id)
            if actor is not None:
                item = KnowledgeItem(
                    fact_id=f"lore_{uid}",
                    content=content,
                    confidence=1.0 if constant else 0.8,
                    source="world_info",
                )
                actors[actor_id] = actor.model_copy(update={"knowledge": [*actor.knowledge, item]})
            elif content not in public:
                public.append(content)
        else:
            if content not in public:
                public.append(content)

    return state.model_copy(
        update={
            "scene": state.scene.model_copy(
                update={"public_facts": public, "hidden_facts": hidden}
            ),
            "actors": actors,
        }
    )
