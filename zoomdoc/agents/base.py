from __future__ import annotations

import json
from typing import Callable, TypeVar

from pydantic import TypeAdapter, ValidationError

from zoomdoc.errors import AgentOutputError
from zoomdoc.llm.json_parser import extract_json
from zoomdoc.llm.vlm_client import Part, VLMClient, VLMResponse
from zoomdoc.observability import TraceLogger

T = TypeVar("T")


def call_with_schema(
    vlm: VLMClient,
    parts: list[Part],
    adapter: TypeAdapter[T],
    *,
    agent: str,
    max_attempts: int,
    logger: TraceLogger,
    validate: Callable[[T], None] | None = None,
) -> tuple[T, VLMResponse, int]:
    """Execute VLM call with automatic JSON extraction, schema validation, and retry with correction.

    Catches (json.JSONDecodeError, ValidationError, ValueError).
    Logs retry events and appends correction dialog to extra_messages.
    Raises AgentOutputError after exceeding max_attempts.
    """
    extra_messages: list[dict] = []
    last_raw = ""
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        with logger.span(agent) as span:
            resp = vlm.generate(parts, extra_messages=extra_messages if extra_messages else None)
            last_raw = resp.text

            try:
                raw_json = extract_json(resp.text)
                obj = adapter.validate_python(raw_json)
                if validate is not None:
                    validate(obj)

                logger.event(
                    span_id=span.span_id,
                    node=agent,
                    status="ok",
                    latency_ms=resp.usage.latency_ms,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    attempt=attempt,
                    payload={"raw": resp.text, "parsed": raw_json},
                )
                return (obj, resp, attempt)

            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.event(
                    span_id=span.span_id,
                    node=agent,
                    status="retry",
                    latency_ms=resp.usage.latency_ms,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    attempt=attempt,
                    payload={"raw": resp.text, "error": last_error},
                )

                if attempt < max_attempts:
                    correction_instruction = (
                        f"Your output was invalid: {last_error}. "
                        "Return only valid JSON strictly matching the requested format."
                    )
                    extra_messages = [
                        {"role": "assistant", "content": resp.text},
                        {"role": "user", "content": correction_instruction},
                    ]

    raise AgentOutputError(
        agent=agent,
        attempts=max_attempts,
        last_raw=last_raw,
        last_error=last_error,
    )
