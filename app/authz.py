"""Workspace/user permission checks."""
from __future__ import annotations

from .config import Settings
from .errors import PermissionDenied


def _check(allowed: dict[str, frozenset[str]], workspace_id: str,
           user_id: str, verb: str) -> None:
    users = allowed.get(workspace_id)
    if users is None:
        raise PermissionDenied(
            f"workspace {workspace_id!r} has no {verb} policy",
            detail={"workspace_id": workspace_id, "user_id": user_id,
                    "reason": f"no_{verb}_policy"},
        )
    if user_id not in users:
        raise PermissionDenied(
            f"user {user_id!r} may not {verb} in workspace {workspace_id!r}",
            detail={"workspace_id": workspace_id, "user_id": user_id,
                    "reason": f"not_{verb.rstrip('er')}_allowed"},
        )


def check_submit(settings: Settings, workspace_id: str, user_id: str) -> None:
    _check(settings.submitters, workspace_id, user_id, "submitter")


def check_approve(settings: Settings, workspace_id: str, user_id: str) -> None:
    _check(settings.approvers, workspace_id, user_id, "approver")
