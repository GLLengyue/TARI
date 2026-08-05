from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .character_cards import detect_locale, parse_card
from .lorebook import normalize_world_book

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

WORLD_CARD_HINTS = {
    "world-card",
    "world",
    "scenario",
    "rpg",
    "multi-npc",
    "multi_npc",
    "simulator",
    "simulation",
    "worldbook",
    "世界卡",
    "世界观",
    "群像",
    "多npc",
    "sandbox",
}

SINGLE_CHARACTER_HINTS = {"单人", "single-character", "single character"}


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", name).strip("._")
    return stem or "resource"


def _rel_id(path: Path) -> str:
    try:
        rel = path.relative_to(Path.cwd())
    except ValueError:
        rel = path
    return str(rel.parent / rel.stem)


def _normalize_world_key(title: str, stem: str = "") -> str:
    """Normalize a world title/stem so cards and lorebooks dedupe."""
    text = re.sub(
        r"\s*(lorebook|world\s*info|世界书|世界观)\s*$",
        "",
        str(title or "").strip().lower(),
    )
    title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)
    stem_key = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+", "", re.sub(r"(\.v[23]|\.worldinfo)$", "", stem.lower())
    )
    return title_key or stem_key


def _is_world_card(card: dict[str, Any]) -> bool:
    """A world card describes a setting/world rather than a single character."""
    tags = {str(x).strip().lower() for x in (card.get("tags") or [])}
    if tags & SINGLE_CHARACTER_HINTS:
        return False
    if tags & WORLD_CARD_HINTS:
        return True
    name = str(card.get("name") or "").lower()
    return any(hint in name for hint in ("world card", "世界卡", "世界观"))


def _card_from_scenario(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the original character-card payload from an imported scenario."""
    if not isinstance(doc, dict) or "campaign_id" not in doc:
        return None
    content = doc
    if isinstance(doc.get("localizations"), dict) and doc["localizations"]:
        content = next(iter(doc["localizations"].values()))
    actor = content.get("actor") if isinstance(content, dict) else None
    if not isinstance(actor, dict):
        return None
    card = dict(actor.get("attributes", {}).get("card") or {})
    if not card:
        return None
    card.setdefault("name", actor.get("name") or doc.get("campaign_id"))
    card.setdefault("description", actor.get("description") or "")
    if "first_mes" not in card:
        card["first_mes"] = content.get("opening") or ""
    return card


@dataclass
class Resource:
    kind: str
    id: str
    path: Path
    title: str
    locale: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "locale": self.locale,
            "tags": self.tags,
            "description": self.description,
            **self.meta,
        }


class ResourceLibrary:
    """Discovers scenarios, character cards, and worlds with precise semantics.

    Kinds are semantic, not file-format based:

    - ``cards``: a narrow single-character definition (e.g. a persona card).
    - ``worlds``: a world setting — world cards (multi-NPC/scenario/simulator
      cards with their embedded lorebook) and standalone world-info books.
    - ``scenarios``: campaign/scenario documents (opening + story framework).

    World cards are deduplicated against standalone lorebooks of the same
    world, preferring the card (it carries the avatar).  Imported scenario
    files that preserve their original card payload are reclassified as cards
    or worlds instead of scenarios.  A PNG card and its same-stem JSON are
    deduplicated, preferring the PNG.
    """

    def __init__(
        self,
        roots: list[Path] | None = None,
        upload_root: str | Path | None = None,
    ) -> None:
        self.upload_root = Path(upload_root or "runtime-data/resources")
        self.roots = roots or self._default_roots()
        if self.upload_root not in self.roots:
            self.roots = [*self.roots, self.upload_root]
        self.by_id: dict[str, Resource] = {}
        self.warnings: list[str] = []

    @staticmethod
    def _default_roots() -> list[Path]:
        roots = [
            Path("examples"),
            Path("materials/foreverse/scenarios"),
            Path("materials/foreverse/zh"),
            Path("materials/foreverse/en"),
            Path("materials/foreverse/world-info"),
            Path("runtime-data/resources"),
        ]
        extra = os.getenv("TARI_RESOURCE_DIRS", "")
        for raw in extra.split(","):
            raw = raw.strip()
            if raw:
                roots.append(Path(raw))
        return roots

    def scan(self) -> None:
        self.by_id = {}
        self.warnings = []
        cards_by_stem: dict[tuple[Path, str], dict[str, Any]] = {}
        scenario_paths: list[Path] = []
        world_paths: list[Path] = []
        self._world_keys: set[str] = set()

        for root in self.roots:
            if not root.is_dir():
                continue
            for kind_dir, kind in (
                ("scenarios", "scenarios"),
                ("cards", "cards"),
                ("worlds", "worlds"),
            ):
                sub = root / kind_dir
                if sub.is_dir():
                    self._scan_dir(
                        sub, kind, cards_by_stem, scenario_paths, world_paths
                    )
            # Auto-detect flat files directly inside the root.
            if root.is_dir():
                self._scan_dir(root, None, cards_by_stem, scenario_paths, world_paths)

        for (_directory, stem), entry in cards_by_stem.items():
            if entry["path"] is None:
                continue
            self._register_card_or_world(entry["path"], stem)

        for path in scenario_paths:
            self._register_scenario_or_card(path)

        for path in world_paths:
            self._register_world_dedup(path)

    def _scan_dir(
        self,
        directory: Path,
        kind_hint: str | None,
        cards_by_stem: dict,
        scenario_paths: list[Path],
        world_paths: list[Path],
    ) -> None:
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            try:
                if kind_hint == "scenarios" or (
                    kind_hint is None and suffix in (".yaml", ".yml")
                ):
                    if self._looks_like_scenario(path):
                        scenario_paths.append(path)
                elif kind_hint == "worlds" or (
                    kind_hint is None and suffix == ".json" and self._looks_like_world(path)
                ):
                    world_paths.append(path)
                elif suffix in (".png", ".json") and self._looks_like_card(path):
                    stem = re.sub(r"\.v[23]$", "", path.stem)
                    key = (directory, stem)
                    entry = cards_by_stem.setdefault(
                        key, {"path": None}
                    )
                    if suffix == ".png":
                        entry["path"] = path
                    elif entry["path"] is None:
                        entry["path"] = path
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the scan
                self.warnings.append(f"{path}: {exc}")

    @staticmethod
    def _looks_like_scenario(path: Path) -> bool:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return False
        return isinstance(doc, dict) and "campaign_id" in doc

    @staticmethod
    def _looks_like_world(path: Path) -> bool:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(doc, dict) and isinstance(doc.get("entries"), (dict, list))

    @staticmethod
    def _looks_like_card(path: Path) -> bool:
        try:
            card = parse_card(path)
        except Exception:
            return False
        return bool(card.get("name"))

    def _register_scenario_or_card(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        card = _card_from_scenario(doc)
        if card is not None:
            name = str(card.get("name") or doc.get("campaign_id"))
            locale = detect_locale(
                str(card.get("description") or ""), str(card.get("first_mes") or "")
            )
            tags = list(card.get("tags") or [])
            description = str(card.get("description") or "")[:300]
            if _is_world_card(card):
                book = card.get("character_book") or {}
                entries = book.get("entries", {})
                count = len(entries) if isinstance(entries, (dict, list)) else 0
                key = _normalize_world_key(name, path.stem)
                if key in self._world_keys:
                    return
                self._add(
                    "worlds",
                    path,
                    name,
                    locale=locale,
                    tags=tags,
                    description=description,
                    meta={"entry_count": count, "source": "scenario"},
                )
                self._world_keys.add(key)
            else:
                self._add(
                    "cards",
                    path,
                    name,
                    locale=locale,
                    tags=tags,
                    description=description,
                    meta={
                        "worldbook": bool(card.get("character_book")),
                        "card_name": path.stem,
                        "scenario_source": True,
                    },
                )
            return

        if "localizations" in doc and isinstance(doc["localizations"], dict):
            locales = list(doc["localizations"])
            first = next(iter(doc["localizations"].values()))
            title = first.get("title") if isinstance(first, dict) else doc.get("campaign_id")
            locale = doc.get("default_locale", locales[0] if locales else None)
        else:
            locales = ["en"]
            title = doc.get("title") or doc.get("campaign_id")
            locale = "en"
        self._add(
            "scenarios",
            path,
            str(title or path.stem),
            locale=str(locale) if locale else None,
            meta={"locales": locales, "fact_count": 0},
        )

    def _register_card_or_world(self, path: Path, stem: str) -> None:
        card = parse_card(path)
        locale = detect_locale(str(card.get("description") or ""), str(card.get("first_mes") or ""))
        name = str(card.get("name") or path.stem)
        tags = list(card.get("tags") or [])
        description = str(card.get("description") or "")[:300]
        if _is_world_card(card):
            book = card.get("character_book") or {}
            entries = book.get("entries", {})
            count = len(entries) if isinstance(entries, (dict, list)) else 0
            self._add(
                "worlds",
                path,
                name,
                locale=locale,
                tags=tags,
                description=description,
                meta={
                    "avatar": path.suffix.lower() == ".png",
                    "entry_count": count,
                    "source": "card",
                },
            )
            self._world_keys.add(_normalize_world_key(name, path.stem))
        else:
            self._add(
                "cards",
                path,
                name,
                locale=locale,
                tags=tags,
                description=description,
                meta={
                    "avatar": path.suffix.lower() == ".png",
                    "worldbook": bool(card.get("character_book")),
                    "card_name": stem,
                },
            )

    def _register_world_dedup(self, path: Path) -> None:
        book = json.loads(path.read_text(encoding="utf-8"))
        name = str(book.get("name") or path.stem)
        key = _normalize_world_key(name, path.stem)
        if key in self._world_keys:
            return
        entries = book.get("entries", {})
        count = len(entries) if isinstance(entries, (dict, list)) else 0
        self._add(
            "worlds",
            path,
            name,
            description=str(book.get("description") or "")[:300],
            meta={"entry_count": count, "source": "lorebook"},
        )
        self._world_keys.add(key)

    def _add(self, kind: str, path: Path, title: str, **kwargs: Any) -> None:
        res = Resource(kind=kind, id=f"{kind}:{_rel_id(path)}", path=path, title=title, **kwargs)
        self.by_id[res.id] = res

    def get(self, kind: str, resource_id: str) -> Resource | None:
        res = self.by_id.get(resource_id)
        return res if res is not None and res.kind == kind else None

    def all(self) -> list[Resource]:
        return list(self.by_id.values())

    def by_kind(self, kind: str) -> list[Resource]:
        return [r for r in self.by_id.values() if r.kind == kind]

    def load_card(self, resource: Resource) -> dict[str, Any]:
        """Load a character-card payload from a PNG/JSON card or imported scenario."""
        if resource.path.suffix.lower() in (".png", ".json"):
            return parse_card(resource.path)
        doc = yaml.safe_load(resource.path.read_text(encoding="utf-8"))
        card = _card_from_scenario(doc)
        if card is None:
            raise ValueError(f"not a character card resource: {resource.path}")
        return card

    def load_world_book(self, resource: Resource) -> dict[str, Any]:
        """Load a world-info book from a lorebook JSON or a world card/scenario."""
        if resource.meta.get("source") in ("card", "scenario"):
            card = self.load_card(resource)
            book = card.get("character_book") or {}
            if not book.get("entries"):
                book = {"name": card.get("name"), "entries": {}}
            return normalize_world_book(book)
        book = json.loads(resource.path.read_text(encoding="utf-8"))
        return normalize_world_book(book)

    # --- uploads ---------------------------------------------------------

    def save_upload(self, kind: str, filename: str, data: bytes) -> Resource:
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
        suffix = Path(filename).suffix.lower()
        stem = _safe_stem(Path(filename).stem)
        self._validate_upload(kind, suffix, data)
        target_dir = self.upload_root / kind
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{stem}{suffix}"
        target.write_bytes(data)
        self.scan()
        res = next(
            (r for r in self.by_id.values() if r.path.resolve() == target.resolve()),
            None,
        )
        if res is None:
            raise ValueError(f"uploaded file could not be registered: {filename}")
        return res

    def _validate_upload(self, kind: str, suffix: str, data: bytes) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / f"upload{suffix}"
            tmp_path.write_bytes(data)
            if kind == "cards":
                if suffix not in (".png", ".json"):
                    raise ValueError("card uploads must be .png or .json")
                card = parse_card(tmp_path)
                if not card.get("name"):
                    raise ValueError("card is missing a name")
            elif kind == "worlds":
                if suffix != ".json":
                    raise ValueError("world-info uploads must be .json")
                try:
                    book = json.loads(tmp_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid world-info JSON: {exc}") from exc
                if not isinstance(book, dict):
                    raise ValueError("world-info must be a JSON object")
                normalize_world_book(book)
                if not isinstance(book.get("entries"), (dict, list)):
                    raise ValueError("world-info must contain an entries list or mapping")
            elif kind == "scenarios":
                if suffix not in (".yaml", ".yml"):
                    raise ValueError("scenario uploads must be .yaml or .yml")
                try:
                    doc = yaml.safe_load(tmp_path.read_text(encoding="utf-8"))
                except yaml.YAMLError as exc:
                    raise ValueError(f"invalid scenario YAML: {exc}") from exc
                if not isinstance(doc, dict) or "campaign_id" not in doc:
                    raise ValueError("scenario must contain a campaign_id")
                if "localizations" not in doc and "title" not in doc:
                    raise ValueError("scenario must contain localizations or a title")
            else:
                raise ValueError(f"unknown resource kind: {kind}")
