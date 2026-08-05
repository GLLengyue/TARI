#!/usr/bin/env python3
"""Extract a card's embedded lorebook into a SillyTavern world-info JSON.

Foreverse character cards ship both as PNG and as V2 JSON.  The JSON carries
the lorebook under ``data.character_book.entries`` as a list; SillyTavern and
``trpg new --world-info`` expect a dict keyed by entry uid.  This script
performs that conversion so the world setting can be imported into TARI.

Usage:
    python3 extract_worldinfo.py card.v2.json [output.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _entry(entry: dict, uid: str) -> dict:
    return {
        "uid": entry.get("id", uid),
        "key": list(entry.get("keys") or entry.get("key") or []),
        "keysecondary": list(entry.get("secondary_keys") or entry.get("keysecondary") or []),
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


def convert(card_path: str | Path) -> dict:
    card = json.loads(Path(card_path).read_text(encoding="utf-8"))
    data = card.get("data") if isinstance(card.get("data"), dict) else card
    book = data.get("character_book") or {}
    entries = book.get("entries") or {}
    converted: dict[str, dict] = {}
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            uid = str(entry.get("id", index))
            converted[uid] = _entry(entry, uid)
    elif isinstance(entries, dict):
        for uid, entry in entries.items():
            converted[str(uid)] = _entry(entry, str(uid))
    return {
        "name": str(book.get("name") or Path(card_path).stem),
        "description": "Extracted from a Foreverse character card; import with `trpg new --world-info`.",
        "scan_depth": 4,
        "entries": converted,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    card_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else card_path.with_suffix(".worldinfo.json")
    out_path.write_text(
        json.dumps(convert(card_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"World info written: {out_path}")


if __name__ == "__main__":
    main()
