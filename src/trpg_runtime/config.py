from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    model_id: str


class AgentConfig(BaseModel):
    model: str
    temperature: float = 0.5
    max_output_tokens: int = 800
    retries: int = Field(default=1, ge=0, le=5)


class RuntimeConfig(BaseModel):
    models: dict[str, ModelConfig]
    agents: dict[str, AgentConfig]


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    with Path(path).open(encoding="utf-8") as f:
        return RuntimeConfig.model_validate(yaml.safe_load(f))
