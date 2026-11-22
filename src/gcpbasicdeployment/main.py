"""FastAPI app factory and router registration."""

import logging

from fastapi import FastAPI

from gcpbasicdeployment.core.config import get_settings
from gcpbasicdeployment.core.exceptions import register_exception_handlers
from gcpbasicdeployment.core.logging import setup_logging
from gcpbasicdeployment.routers import health

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="LinkRot Watchdog")
    register_exception_handlers(app)
    app.include_router(health.router)
    logger.info("app created")
    return app


app = create_app()
