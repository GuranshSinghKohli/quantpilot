import json
import os
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

MODEL = "gpt-4o-mini"


def _client() -> Optional[AsyncOpenAI]:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return AsyncOpenAI()


async def call_openai_text(
    system_prompt: str,
    user_prompt: str,
    fallback: str = "",
) -> str:
    client = _client()
    if client is None:
        return fallback

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        content = response.choices[0].message.content
        return content.strip() if content else fallback
    except Exception:
        return fallback

async def call_openai_json(
    system_prompt: str,
    user_prompt: str,
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    client = _client()
    if client is None:
        return fallback

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        content = response.choices[0].message.content
        if not content:
            return fallback
        return json.loads(content)
    except Exception:
        return fallback
