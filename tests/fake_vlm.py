from __future__ import annotations

from zoomdoc.llm.vlm_client import Part, VLMResponse
from zoomdoc.schemas import VLMUsage


class FakeVLM:
    """Mock VLM client returning predefined scripted responses for deterministic testing."""

    def __init__(self, scripted: list[str]) -> None:
        self.scripted = list(scripted)
        self.calls: list[list[Part]] = []
        self.extra_messages_history: list[list[dict] | None] = []
        self.call_count = 0

    def generate(
        self, parts: list[Part], *, extra_messages: list[dict] | None = None
    ) -> VLMResponse:
        self.calls.append(list(parts))
        self.extra_messages_history.append(list(extra_messages) if extra_messages else None)

        if self.call_count >= len(self.scripted):
            raise IndexError(
                f"FakeVLM ran out of scripted responses: called {self.call_count + 1} times, "
                f"only {len(self.scripted)} scripted."
            )

        text = self.scripted[self.call_count]
        self.call_count += 1

        usage = VLMUsage(
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=10.0,
            model="fake-vlm-model",
        )
        return VLMResponse(text=text, usage=usage)
