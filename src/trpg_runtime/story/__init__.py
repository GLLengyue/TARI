"""Story bundle models and source import helpers."""

from typing import Any

from .bundle import StoryBeat, StoryBundle, load_bundle, write_bundle
from .importer import SourceChapter, SourceDocument, parse_source, scaffold_bundle

_LAZY_COMPILER_EXPORTS = {
    "ChapterCard",
    "CompilationResult",
    "StoryArcRecord",
    "StoryCompilationWorkspace",
    "compile_document",
    "compile_source",
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_COMPILER_EXPORTS:
        raise AttributeError("module " + __name__ + " has no attribute " + name)
    from . import decomposer

    value = getattr(decomposer, name)
    globals()[name] = value
    return value


__all__ = [
    "SourceChapter",
    "SourceDocument",
    "StoryBundle",
    "StoryBeat",
    "ChapterCard",
    "CompilationResult",
    "StoryArcRecord",
    "StoryCompilationWorkspace",
    "compile_document",
    "compile_source",
    "load_bundle",
    "parse_source",
    "scaffold_bundle",
    "write_bundle",
]
