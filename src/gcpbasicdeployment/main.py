"""FastAPI app factory and router registration."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gcpbasicdeployment.core.config import get_settings
from gcpbasicdeployment.core.exceptions import register_exception_handlers
from gcpbasicdeployment.core.logging import setup_logging
from gcpbasicdeployment.db.session import create_tables
from gcpbasicdeployment.routers import health, links

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await create_tables()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="LinkRot Watchdog", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(links.router)
    logger.info("app created")
    return app


app = create_app()
