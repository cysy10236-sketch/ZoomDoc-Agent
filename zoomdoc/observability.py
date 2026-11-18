from __future__ import annotations

import datetime
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from zoomdoc.errors import ZoomDocError

EventCallback = Callable[[dict[str, Any]], None]


class Span:
    def __init__(self, logger: TraceLogger, node: str) -> None:
        self.logger = logger
        self.node = node
        self.span_id = uuid.uuid4().hex[:12]
        self._t0 = time.perf_counter()

    def __enter__(self) -> Span:
        self._t0 = time.perf_counter()
        return self

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class TraceLogger:
    """Thread-safe structured JSONL trace logger.

    Writes events to {runs_dir}/{trace_id}/trace.jsonl.
    """

    def __init__(
        self,
        runs_dir: str,
        trace_id: str,
        on_event: EventCallback | None = None,
        *,
        reset: bool = False,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.trace_id = trace_id
        self.trace_dir = self.runs_dir / trace_id
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.trace_dir / "trace.jsonl"
        if reset and self.log_file.is_file():
            self.log_file.unlink(missing_ok=True)
        self.on_event = on_event
        self._lock = threading.Lock()

    def span(self, node: str) -> Span:
        return Span(self, node)

    def stage(self, stage: str, payload: dict[str, Any]) -> None:
        """Emit live stage telemetry to in-memory subscriber without altering trace.jsonl."""
        if self.on_event is None:
            return
        evt = {
            "event_type": "stage",
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "stage": stage,
            "payload": payload,
        }
        self.on_event(evt)

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
        namespace: str = "main",
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
            "namespace": namespace,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "attempt": attempt,
            "payload": clean_payload,
        }

        record_str = json.dumps(record, ensure_ascii=False)
        if "data:image" in record_str:
            raise ZoomDocError("base64 image leaked into trace")

        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(record_str + "\n")

            if self.on_event is not None:
                self.on_event({"event_type": "trace", **record})
