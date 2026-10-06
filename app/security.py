"""Callback authenticity: HMAC signature + timestamp freshness."""
from __future__ import annotations

import hashlib
import hmac

from .config import Settings
from .errors import SignatureError, StaleRequestError


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """sig = hex(HMAC_SHA256(secret, f"{timestamp}.{body}"))"""
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b".")
    mac.update(body)
    return mac.hexdigest()


def verify_signature(settings: Settings, workspace_id: str, timestamp: str,
                     signature: str, body: bytes, now: float) -> None:
    """Raise SignatureError / StaleRequestError unless the request is authentic.

    Every failure path raises a distinct error carrying enough detail to
    locate the offending request, without leaking secrets.
    """
    secret = settings.workspace_secrets.get(workspace_id)
    if secret is None:
        raise SignatureError(
            f"unknown workspace {workspace_id!r}",
            detail={"workspace_id": workspace_id, "reason": "unknown_workspace"},
        )
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        raise SignatureError(
            "malformed timestamp header",
            detail={"workspace_id": workspace_id, "reason": "bad_timestamp"},
        ) from None
    skew = abs(now - ts)
    if skew > settings.max_clock_skew_seconds:
        raise StaleRequestError(
            f"timestamp skew {skew:.1f}s exceeds limit",
            detail={"workspace_id": workspace_id, "reason": "stale",
                    "skew_seconds": skew},
        )
    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature or ""):
        raise SignatureError(
            "signature mismatch",
            detail={"workspace_id": workspace_id, "reason": "bad_signature"},
        )
