"""Workspace/user permission model.

Roles are configured per workspace and map to the command names a role may
submit (``"*"`` is a wildcard). Approvers are an explicit allow-list per
workspace; optionally the submitter may not approve their own command.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Policy:
    # workspace -> role -> allowed command names ("*" wildcard allowed).
    roles: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # workspace -> list of approver user ids.
    approvers: dict[str, list[str]] = field(default_factory=dict)
    require_different_approver: bool = True


def can_submit(
    policy: Policy, workspace_id: str, role: str, command_name: str
) -> bool:
    workspace = policy.roles.get(workspace_id, {})
    allowed = workspace.get(role, [])
    return "*" in allowed or command_name in allowed


def can_approve(
    policy: Policy, workspace_id: str, user_id: str, submitter_id: str
) -> bool:
    if user_id not in policy.approvers.get(workspace_id, []):
        return False
    if policy.require_different_approver and user_id == submitter_id:
        return False
    return True
