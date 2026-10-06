"""Request source and signature verification.

The platform signs each callback with a shared secret::

    signature = hex(hmac_sha256(secret, f"{timestamp}.{body}"))

Verification enforces:

* presence of timestamp and signature headers,
* timestamp inside ``tolerance_seconds`` of current time (replay/expiry),
* constant-time equality of signatures.

Every failure raises a distinct error type carrying the request context so
callers can log the exact reason.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from gateway.errors import SignatureError, TimestampExpiredError


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    tolerance_seconds: int,
    now: int | None = None,
) -> None:
    """Raise SignatureError / TimestampExpiredError on failure."""
    if not timestamp:
        raise SignatureError("missing timestamp header")
    if not signature:
        raise SignatureError("missing signature header")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("malformed timestamp") from exc

    current = int(time.time()) if now is None else now
    if abs(current - ts) > tolerance_seconds:
        raise TimestampExpiredError(
            f"timestamp {ts} outside tolerance of {tolerance_seconds}s "
            f"(server time {current})"
        )

    expected = compute_signature(secret, timestamp, body)
    # Platform may send a "sha256=" prefix.
    provided = signature[len("sha256=") :] if signature.startswith("sha256=") else signature
    if not hmac.compare_digest(expected, provided.strip()):
        raise SignatureError("signature mismatch")
