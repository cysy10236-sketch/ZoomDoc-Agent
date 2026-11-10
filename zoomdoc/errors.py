from __future__ import annotations


class ZoomDocError(Exception):
    """Base exception for all ZoomDoc-Agent errors."""


class AgentOutputError(ZoomDocError):
    """Raised when an Agent fails to produce valid structured output after max_attempts."""

    def __init__(self, agent: str, attempts: int, last_raw: str, last_error: str) -> None:
        self.agent = agent
        self.attempts = attempts
        self.last_raw = last_raw
        self.last_error = last_error
        msg = (
            f"Agent '{agent}' failed after {attempts} attempts. "
            f"Last error: {last_error}\nLast raw output: {last_raw[:500]}"
        )
        super().__init__(msg)


class GeometryError(ZoomDocError):
    """Raised when bounding box or point geometry is degenerate, crosses cell boundaries, or hits dividers."""


class BudgetExceeded(ZoomDocError):
    """Raised when maximum step budget or zoom operations are exhausted."""
