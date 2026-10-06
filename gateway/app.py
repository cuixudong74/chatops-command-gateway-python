"""HTTP layer: FastAPI callback endpoint.

Endpoints
---------
``POST /callback``
    Platform webhook. Raw body is verified against the ``X-Signature`` /
    ``X-Timestamp`` headers *before* JSON parsing. Any failure produces a
    401/400 and a row in ``security_log`` keyed by the returned request id.

``GET /commands/{command_id}``   - status + audit trail
``GET /audit``                   - recent audit entries
``GET /security-log``            - recent rejected requests
``GET /healthz``                 - liveness
"""
from __future__ import annotations

import contextlib
import json
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.config import Settings, load_settings
from gateway.errors import GatewayError
from gateway.models import CallbackPayload
from gateway.security import verify_signature
from gateway.service import GatewayService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    service = GatewayService(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(
        title="chatops-command-gateway", version="0.1.0", lifespan=lifespan
    )
    app.state.service = service
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    def log_security(request_id: str, reason: str, detail: dict) -> None:
        try:
            service.db.connect()
            with service.db.lock, service._tx():
                service.audit.append_security(request_id, reason, detail=detail)
        except Exception:  # logging failure must never mask the rejection
            pass

    @app.post("/callback")
    async def callback(request: Request):
        request_id = uuid.uuid4().hex
        body = await request.body()
        try:
            verify_signature(
                settings.signing_secret,
                request.headers.get("x-timestamp"),
                request.headers.get("x-signature"),
                body,
                settings.signature_tolerance_seconds,
            )
        except GatewayError as exc:
            log_security(
                request_id,
                exc.code,
                {
                    "reason": str(exc),
                    "path": str(request.url.path),
                    "peer": request.client.host if request.client else None,
                    "content_length": len(body),
                },
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": exc.code,
                    "request_id": request_id,
                    "message": str(exc),
                },
            )

        try:
            data = json.loads(body)
            payload = CallbackPayload.model_validate(data)
        except Exception as exc:
            log_security(request_id, "malformed_payload", {"reason": str(exc)})
            return JSONResponse(
                status_code=400,
                content={
                    "error": "malformed_payload",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )

        try:
            result = await service.handle_callback(payload)
            return {"request_id": request_id, **result}
        except GatewayError as exc:
            with service.db.lock, service._tx():
                service.audit.append(
                    payload.command_id,
                    "callback_rejected",
                    actor=payload.user_id,
                    result=exc.code,
                    related_id=payload.event_id,
                    detail={"reason": str(exc)},
                )
            status = 429 if exc.code == "rate_limited" else 403
            return JSONResponse(
                status_code=status,
                content={
                    "error": exc.code,
                    "request_id": request_id,
                    "command_id": payload.command_id,
                    "message": str(exc),
                },
            )

    @app.get("/commands/{command_id}")
    async def command_status(command_id: str) -> dict:
        data = service.get_command_status(command_id)
        if data is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        data["audit"] = service.audit.list_for_command(command_id)
        return data

    @app.get("/audit")
    async def list_audit(limit: int = 100) -> list:
        return service.audit.list_all(limit)

    @app.get("/security-log")
    async def list_security(limit: int = 100) -> list:
        return service.audit.list_security(limit)

    return app
