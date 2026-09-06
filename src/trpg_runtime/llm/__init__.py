"""Shared model-provider boundary used by both application contexts."""

from .client import (
    LLMSettings,
    OpenAICompatibleClient,
    extract_json_object,
    resolve_llm_settings,
)

__all__ = [
    "LLMSettings",
    "OpenAICompatibleClient",
    "extract_json_object",
    "resolve_llm_settings",
]
