"""Entry point: wire config, store, executor, service and HTTP app."""
import logging
import time
from typing import Any

import uvicorn

from app.api import create_app
from app.config import Settings
from app.models import CommandRecord
from app.ratelimit import RateLimiter
from app.service import GatewayService
from app.storage import Store

log = logging.getLogger("gateway")


class LoggingExecutor:
    """Placeholder executor: real ops integrations plug in here.

    MUST be idempotent per command_id (the gateway may re-drive execution
    after a crash between persisting EXECUTING and finishing the call).
    """

    def execute(self, record: CommandRecord) -> dict[str, Any]:
        log.info("execute command=%s action=%s args=%s",
                 record.command_id, record.action, record.args)
        return {"executed": True, "action": record.action}


def build(settings: Settings | None = None) -> tuple:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    limiter = RateLimiter(settings.rate_limit_max,
                          settings.rate_limit_window_seconds)
    service = GatewayService(settings, store, LoggingExecutor(), limiter,
                             clock=time.time)
    resumed = service.recover()
    if resumed:
        log.info("recovered %d unfinished commands", len(resumed))
    return create_app(service, settings), service


app, _service = build()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
