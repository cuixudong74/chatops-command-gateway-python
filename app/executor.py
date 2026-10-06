"""Side-effect executor. Real ops plug in here; tests use a recording stub."""
from __future__ import annotations

from typing import Any, Protocol

from .models import CommandRecord


class Executor(Protocol):
    def execute(self, record: CommandRecord) -> dict[str, Any]:
        """Perform the side effect exactly once. Returns a result payload."""
        ...
