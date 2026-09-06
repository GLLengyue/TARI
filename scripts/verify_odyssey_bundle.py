"""Offline smoke test for the Odyssey Story Bundle produced by the detached job.

Validates that the resumable compiler produced a loadable Story Bundle,
loads it through the same loader Story Mode uses, and prints high-level
resource statistics. Intended to be run after the detached job has either
succeeded or partially completed (the manifest is checked even on partial
runs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "runtime-data" / "story-books" / "odyssey"
BUNDLE = WORKSPACE / "bundle.yaml"
MANIFEST = WORKSPACE / "manifest.json"
WORLD_INFO = WORKSPACE / "world-info.json"


def main() -> int:
    if not MANIFEST.is_file():
        print(f"FAIL: manifest missing at {MANIFEST}")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print("manifest.status:", manifest.get("status"))
    print("manifest.stages:", manifest.get("stages"))
    print("manifest.model:", manifest.get("settings", {}).get("model"))

    if not BUNDLE.is_file():
        print(f"FAIL: bundle missing at {BUNDLE}")
        return 1
    from trpg_runtime.story import load_bundle

    bundle = load_bundle(BUNDLE)
    print(
        f"bundle: {bundle.title} ({bundle.story_id}) "
        f"beats={len(bundle.story_beats)} "
        f"entities={len(bundle.entities)} "
        f"facts={len(bundle.canon_facts)} "
        f"relationships={len(bundle.relationships)} "
        f"arcs={len(bundle.plot_arcs)}"
    )
    if not bundle.story_beats:
        print("FAIL: bundle has no story beats")
        return 1

    if WORLD_INFO.is_file():
        book = json.loads(WORLD_INFO.read_text(encoding="utf-8"))
        entries = book.get("entries") or {}
        print(f"world-info entries: {len(entries)}")
    else:
        print("NOTE: world-info.json not yet produced")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
