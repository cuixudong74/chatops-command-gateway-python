"""Domain error hierarchy for the gateway."""
from __future__ import annotations


class GatewayError(Exception):
    """Base class for all gateway errors (carries a stable code)."""

    code = "gateway_error"


class SignatureError(GatewayError):
    code = "invalid_signature"


class TimestampExpiredError(GatewayError):
    code = "timestamp_expired"


class DuplicateCallback(GatewayError):
    code = "duplicate_callback"


class CommandNotFound(GatewayError):
    code = "command_not_found"


class InvalidStateTransition(GatewayError):
    code = "invalid_state_transition"


class ApprovalError(GatewayError):
    code = "approval_not_allowed"


class PermissionDenied(GatewayError):
    code = "permission_denied"


class RateLimited(GatewayError):
    code = "rate_limited"


class UnknownCommand(GatewayError):
    code = "unknown_command"
