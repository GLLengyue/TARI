#!/usr/bin/env python3
"""Detached diagnostic for the resumable Odyssey world merge stage."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from trpg_runtime.story.decomposer import _world_merge_prompt

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "runtime-data" / "story-books" / "odyssey"
OUTPUT = REPO / "logs" / "odyssey" / "world_merge_0003_probe.json"
BASE_URL = (
    os.environ.get("TARI_LLM_BASE_URL")
    or os.environ.get("EVOT_LLM_OPENAI_BASE_URL")
    or "http://127.0.0.1:8080/v1"
)
MODEL = (
    os.environ.get("TARI_LLM_MODEL")
    or os.environ.get("EVOT_LLM_OPENAI_MODEL")
    or "qwen3.8-27b"
)
API_KEY = os.environ.get("TARI_LLM_API_KEY") or os.environ.get(
    "EVOT_LLM_OPENAI_API_KEY", "no-key-needed"
)


def _load_content(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload["content"])


def _write(payload: object) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(OUTPUT)


async def main() -> None:
    previous = _load_content(WORKSPACE / "world_cards" / "world_merge_0002.json")
    current = _load_content(WORKSPACE / "world_cards" / "world_card_0003.json")
    messages = _world_merge_prompt(previous, current, "资料卡 3/4")
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 6000,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.time()
    result: dict[str, object] = {
        "request": {
            "base_url": BASE_URL,
            "model": MODEL,
            "prompt_chars": sum(len(message["content"]) for message in messages),
            "user_chars": len(messages[-1]["content"]),
            "max_tokens": 6000,
            "chat_template_kwargs": body["chat_template_kwargs"],
        }
    }
    try:
        timeout = httpx.Timeout(600.0, connect=15.0)
        async with httpx.AsyncClient(
            base_url=BASE_URL.rstrip("/") + "/",
            timeout=timeout,
            headers={
                "Authorization": "Bearer " + API_KEY,
                "Content-Type": "application/json",
            },
            trust_env=False,
        ) as client:
            response = await client.post("chat/completions", json=body)
        result["http_status"] = response.status_code
        payload = response.json()
        result["response"] = payload
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning_content") or "")
        result["diagnostic"] = {
            "finish_reason": choices[0].get("finish_reason") if choices else None,
            "message_keys": sorted(message),
            "content_chars": len(content),
            "reasoning_chars": len(reasoning),
            "content_head": content[:500],
            "reasoning_head": reasoning[:500],
            "usage": payload.get("usage"),
        }
    except Exception as exc:  # noqa: BLE001 - persist detached probe failures
        result["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        result["elapsed_seconds"] = round(time.time() - started, 3)
        _write(result)
        diagnostic = result.get("diagnostic") or result.get("exception") or {}
        print(
            json.dumps(
                {"elapsed_seconds": result["elapsed_seconds"], **diagnostic},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
