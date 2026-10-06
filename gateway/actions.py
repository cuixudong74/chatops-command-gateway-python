"""Executable operational actions.

Handlers MUST be idempotent: because a crash may happen after a side effect
but before the status is persisted, recovery may invoke the same handler
again for the same ``command_id``. A handler should use the stable
``command_id`` as its idempotency key when talking to downstream systems.

Deployments can register extra handlers via :func:`register_action`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ActionResult:
    success: bool
    output: dict[str, Any]


class ActionHandler(Protocol):
    def __call__(
        self, command_name: str, args: dict[str, Any], command_id: str
    ) -> ActionResult: ...


_REGISTRY: dict[str, ActionHandler] = {}


def register_action(name: str, handler: ActionHandler) -> None:
    _REGISTRY[name] = handler


def get_handler(command_name: str) -> ActionHandler | None:
    return _REGISTRY.get(command_name)


def _noop(command_name: str, args: dict[str, Any], command_id: str) -> ActionResult:
    """Default in-memory implementation used for built-in demo commands."""
    return ActionResult(success=True, output={"echo_args": args})


# Built-in demo commands (in real deployments register real executors).
for _name in ("restart_service", "clear_cache", "run_job", "status"):
    register_action(_name, _noop)
