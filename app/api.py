"""FastAPI surface: callback ingress + query endpoints."""
from __future__ import annotations

import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import GatewayError, SignatureError
from .models import AuditEvent, CallbackEnvelope, EventType
from .security import verify_signature
from .service import GatewayService


def create_app(service: GatewayService, settings: Settings) -> FastAPI:
    app = FastAPI(title="chatops-command-gateway")

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_req: Request, exc: GatewayError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.code, "message": str(exc), "detail": exc.detail},
        )

    @app.post("/callbacks")
    async def callbacks(request: Request):
        body = await request.body()
        workspace_id = request.headers.get("x-workspace-id", "")
        timestamp = request.headers.get("x-timestamp", "")
        signature = request.headers.get("x-signature", "")
        # Best-effort identifiers so even rejected requests are auditable.
        delivery_id, command_id, actor_id = "", "", ""
        try:
            peek = json.loads(body or b"{}")
            delivery_id = str(peek.get("delivery_id", ""))
            command_id = str(peek.get("command_id", ""))
            actor_id = str(peek.get("actor_id", ""))
        except (ValueError, AttributeError):
            pass
        try:
            verify_signature(settings, workspace_id, timestamp, signature,
                             body, now=time.time())
        except GatewayError as exc:
            service._store.append_audit(AuditEvent(
                seq=0, ts=time.time(), command_id=command_id or "unknown",
                delivery_id=delivery_id or "unknown",
                actor_id=actor_id or "unknown",
                event="request_rejected",
                detail={"error": exc.code, **exc.detail}))
            raise
        try:
            payload = json.loads(body)
            env = CallbackEnvelope(
                delivery_id=str(payload["delivery_id"]),
                event_type=EventType(payload["event_type"]),
                workspace_id=workspace_id,
                actor_id=str(payload["actor_id"]),
                command_id=str(payload["command_id"]),
                action=str(payload["action"]),
                args=dict(payload.get("args") or {}),
                occurred_at=float(payload.get("occurred_at", 0.0)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise SignatureError(f"malformed callback body: {exc}",
                                 detail={"reason": "bad_body"}) from None
        return service.handle_callback(env)

    @app.get("/commands/{command_id}")
    async def get_command(command_id: str):
        rec = service._store.get_command(command_id)
        if rec is None:
            return JSONResponse(status_code=404,
                                content={"error": "command_not_found"})
        return {
            "command_id": rec.command_id,
            "workspace_id": rec.workspace_id,
            "action": rec.action,
            "args": rec.args,
            "status": rec.status.value,
            "created_by": rec.created_by,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "approved_by": rec.approved_by,
            "rejected_by": rec.rejected_by,
            "result": rec.result,
            "version": rec.version,
        }

    @app.get("/commands/{command_id}/audit")
    async def get_command_audit(command_id: str):
        return {"audit": [e.__dict__ for e in service._store.list_audit(command_id)]}

    @app.get("/audit")
    async def get_audit():
        return {"audit": [e.__dict__ for e in service._store.list_audit()]}

    return app
