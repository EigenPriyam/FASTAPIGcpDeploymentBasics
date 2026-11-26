"""Queries for the checks table (and the link status that a check updates)."""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.core.exceptions import NotFoundError
from gcpbasicdeployment.db.models import Check, Link
from gcpbasicdeployment.repositories import links


async def get_target(session: AsyncSession, link_id: int) -> tuple[int, str]:
    """(id, url) of one link. Ends the transaction before the caller goes online."""
    link = await links.get(session, link_id)
    target = (link.id, link.url)
    await session.commit()
    return target


async def list_targets(session: AsyncSession) -> list[tuple[int, str]]:
    """(id, url) of every link. Ends the transaction before the caller goes online."""
    result = await session.execute(select(Link.id, Link.url).order_by(Link.id))
    targets = [(row.id, row.url) for row in result]
    await session.commit()
    return targets


async def save(
    session: AsyncSession,
    link_id: int,
    *,
    last_status: str,
    status_code: int | None,
    latency_ms: int | None,
    error: str | None,
) -> Check:
    """Insert a check and update the link's status in one transaction."""
    updated = await session.scalar(
        update(Link)
        .where(Link.id == link_id)
        .values(last_status=last_status)
        .returning(Link.id)
    )
    if updated is None:
        await session.rollback()
        raise NotFoundError(f"Link {link_id} not found")
    check = Check(
        link_id=link_id, status_code=status_code, latency_ms=latency_ms, error=error
    )
    session.add(check)
    await session.commit()
    await session.refresh(check)  # load server-generated checked_at
    return check


async def list_for_link(session: AsyncSession, link_id: int) -> Sequence[Check]:
    await links.get(session, link_id)  # 404 for an unknown link
    result = await session.scalars(
        select(Check)
        .where(Check.link_id == link_id)
        .order_by(Check.checked_at.desc(), Check.id.desc())
    )
    return result.all()
