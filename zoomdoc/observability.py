from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any, Literal


class Span:
    def __init__(self, logger: TraceLogger, node: str) -> None:
        self.logger = logger
        self.node = node
        self.span_id = uuid.uuid4().hex[:12]

    def __enter__(self) -> Span:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class TraceLogger:
    """Thread-safe structured JSONL trace logger.

    Writes events to {runs_dir}/{trace_id}/trace.jsonl.
    """

    def __init__(self, runs_dir: str, trace_id: str) -> None:
        self.runs_dir = Path(runs_dir)
        self.trace_id = trace_id
        self.trace_dir = self.runs_dir / trace_id
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.trace_dir / "trace.jsonl"

    def span(self, node: str) -> Span:
        return Span(self, node)

    def event(
        self,
        *,
        span_id: str,
        node: str,
        status: Literal["ok", "retry", "error"],
        latency_ms: float,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        attempt: int,
        payload: dict,
    ) -> None:
        # Sanitize raw output truncation if present
        clean_payload = dict(payload)
        if "raw" in clean_payload and isinstance(clean_payload["raw"], str):
            raw_str = clean_payload["raw"]
            clean_payload["raw_len"] = len(raw_str)
            clean_payload["raw"] = raw_str[:4000]

        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "span_id": span_id,
            "node": node,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "attempt": attempt,
            "payload": clean_payload,
        }

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
