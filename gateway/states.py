"""Command state machine.

Allowed transitions::

    SUBMITTED        -> PENDING_APPROVAL
    PENDING_APPROVAL -> APPROVED | REJECTED
    APPROVED         -> EXECUTING
    EXECUTING        -> SUCCEEDED | FAILED
    REJECTED / SUCCEEDED / FAILED are terminal.

Any other transition (skipping steps, moving backwards, acting twice on a
terminal command) is rejected.
"""
from __future__ import annotations

from gateway.errors import InvalidStateTransition
from gateway.models import CommandStatus

ALLOWED_TRANSITIONS: dict[CommandStatus, set[CommandStatus]] = {
    CommandStatus.SUBMITTED: {CommandStatus.PENDING_APPROVAL},
    CommandStatus.PENDING_APPROVAL: {
        CommandStatus.APPROVED,
        CommandStatus.REJECTED,
    },
    CommandStatus.APPROVED: {CommandStatus.EXECUTING},
    CommandStatus.EXECUTING: {
        CommandStatus.SUCCEEDED,
        CommandStatus.FAILED,
    },
    CommandStatus.REJECTED: set(),
    CommandStatus.SUCCEEDED: set(),
    CommandStatus.FAILED: set(),
}


def can_transition(src: CommandStatus, dst: CommandStatus) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, set())


def require_transition(src: CommandStatus, dst: CommandStatus) -> None:
    if not can_transition(src, dst):
        raise InvalidStateTransition(
            f"illegal transition {src.value} -> {dst.value}"
        )
