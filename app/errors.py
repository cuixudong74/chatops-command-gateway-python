"""Typed errors with machine-distinguishable reason codes."""
from __future__ import annotations


class GatewayError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message: str = "", *, detail: dict | None = None):
        super().__init__(message or self.code)
        self.detail = detail or {}


class SignatureError(GatewayError):
    code = "invalid_signature"
    http_status = 401


class StaleRequestError(GatewayError):
    code = "stale_request"
    http_status = 401


class PermissionDenied(GatewayError):
    code = "permission_denied"
    http_status = 403


class RateLimited(GatewayError):
    code = "rate_limited"
    http_status = 429


class IllegalTransition(GatewayError):
    code = "illegal_transition"
    http_status = 409


class DuplicateDelivery(GatewayError):
    code = "duplicate_delivery"
    http_status = 200


class CommandNotFound(GatewayError):
    code = "command_not_found"
    http_status = 404
