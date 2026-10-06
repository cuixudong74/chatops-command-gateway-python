"""Approval command state machine: legal transitions only."""
from __future__ import annotations

from .models import CommandStatus

# The single source of truth for legal transitions.
TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.RECEIVED: frozenset({CommandStatus.SUBMITTED}),
    CommandStatus.SUBMITTED: frozenset({CommandStatus.PENDING_APPROVAL}),
    CommandStatus.PENDING_APPROVAL: frozenset({
        CommandStatus.APPROVED,
        CommandStatus.REJECTED,
    }),
    CommandStatus.APPROVED: frozenset({CommandStatus.EXECUTING}),
    CommandStatus.EXECUTING: frozenset({
        CommandStatus.SUCCEEDED,
        CommandStatus.FAILED,
    }),
    CommandStatus.REJECTED: frozenset(),
    CommandStatus.SUCCEEDED: frozenset(),
    CommandStatus.FAILED: frozenset(),
}

TERMINAL_STATES: frozenset[CommandStatus] = frozenset(
    s for s, nxt in TRANSITIONS.items() if not nxt
)


def can_transition(src: CommandStatus, dst: CommandStatus) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())
