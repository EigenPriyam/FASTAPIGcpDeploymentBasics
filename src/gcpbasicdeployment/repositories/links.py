"""CRUD queries for the links table. All SQL for links lives here."""

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.core.exceptions import DuplicateError, NotFoundError
from gcpbasicdeployment.db.models import Link

logger = logging.getLogger(__name__)


async def create(session: AsyncSession, url: str, label: str | None) -> Link:
    link = Link(url=url, label=label)
    session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise DuplicateError("A link with this URL already exists") from None
    await session.refresh(link)  # load server-generated created_at
    logger.info("link created id=%s", link.id)
    return link


async def list_all(session: AsyncSession) -> Sequence[Link]:
    result = await session.scalars(
        select(Link).order_by(Link.created_at.desc(), Link.id.desc())
    )
    return result.all()


async def get(session: AsyncSession, link_id: int) -> Link:
    link = await session.get(Link, link_id)
    if link is None:
        raise NotFoundError(f"Link {link_id} not found")
    return link


async def delete(session: AsyncSession, link_id: int) -> None:
    link = await get(session, link_id)
    await session.delete(link)
    await session.commit()
    logger.info("link deleted id=%s", link_id)
