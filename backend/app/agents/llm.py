import json
import logging
import os
import time
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from app.config import MAX_RETRIES
from app.observability.logger import get_logger, log_event
from app.security.llm_budget import allow_llm_call, record_llm_call

MODEL = "gpt-4o-mini"
logger = get_logger("llm")


def _client() -> Optional[AsyncOpenAI]:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return AsyncOpenAI()


def _usage_tokens(response: Any) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_tokens", 0) or 0)


async def call_openai_text(
    system_prompt: str,
    user_prompt: str,
    fallback: str = "",
) -> str:
    client = _client()
    if client is None:
        return fallback
    if not allow_llm_call():
        log_event(logger, logging.WARNING, "LLM call blocked by investigation budget", event="LLM_BUDGET_EXCEEDED")
        return fallback

    last_error: Optional[Exception] = None
    for attempt in range(max(0, MAX_RETRIES) + 1):
        start = time.perf_counter()
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            tokens = _usage_tokens(response)
            record_llm_call(tokens=tokens, model=MODEL, latency_ms=latency_ms)
            log_event(
                logger,
                logging.INFO,
                "LLM call completed",
                event="LLM_CALL_COMPLETED",
                model=MODEL,
                tokens=tokens,
                latency_ms=latency_ms,
                attempt=attempt + 1,
            )
            content = response.choices[0].message.content
            return content.strip() if content else fallback
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                continue
    log_event(
        logger,
        logging.WARNING,
        "LLM call failed",
        event="LLM_CALL_FAILED",
        error=str(last_error) if last_error else "",
    )
    return fallback


async def call_openai_json(
    system_prompt: str,
    user_prompt: str,
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    client = _client()
    if client is None:
        return fallback
    if not allow_llm_call():
        log_event(logger, logging.WARNING, "LLM call blocked by investigation budget", event="LLM_BUDGET_EXCEEDED")
        return fallback

    last_error: Optional[Exception] = None
    for attempt in range(max(0, MAX_RETRIES) + 1):
        start = time.perf_counter()
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
            latency_ms = int((time.perf_counter() - start) * 1000)
            tokens = _usage_tokens(response)
            record_llm_call(tokens=tokens, model=MODEL, latency_ms=latency_ms)
            log_event(
                logger,
                logging.INFO,
                "LLM call completed",
                event="LLM_CALL_COMPLETED",
                model=MODEL,
                tokens=tokens,
                latency_ms=latency_ms,
                attempt=attempt + 1,
            )
            content = response.choices[0].message.content
            if not content:
                return fallback
            return json.loads(content)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                continue
    log_event(
        logger,
        logging.WARNING,
        "LLM call failed",
        event="LLM_CALL_FAILED",
        error=str(last_error) if last_error else "",
    )
    return fallback
