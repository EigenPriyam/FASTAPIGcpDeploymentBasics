"""Queries for the checks table (and the link fields that a check updates)."""

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.core.exceptions import NotFoundError
from gcpbasicdeployment.db.models import Check, Link
from gcpbasicdeployment.repositories import links


class Target(NamedTuple):
    id: int
    url: str
    archive_url: str | None


async def get_target(session: AsyncSession, link_id: int) -> Target:
    """One link to check. Ends the transaction before the caller goes online."""
    link = await links.get(session, link_id)
    target = Target(link.id, link.url, link.archive_url)
    await session.commit()
    return target


async def list_targets(session: AsyncSession) -> list[Target]:
    """Every link to check. Ends the transaction before the caller goes online."""
    result = await session.execute(
        select(Link.id, Link.url, Link.archive_url).order_by(Link.id)
    )
    targets = [Target(row.id, row.url, row.archive_url) for row in result]
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
    new_archive_url: str | None = None,
    clear_archive_url: bool = False,
) -> tuple[Check, str | None]:
    """Insert a check and update the link in one transaction.

    Returns the check and the link's stored archive_url, which is only written when
    setting or clearing it.
    """
    values: dict[str, str | None] = {"last_status": last_status}
    if new_archive_url is not None or clear_archive_url:
        values["archive_url"] = new_archive_url
    result = await session.execute(
        update(Link)
        .where(Link.id == link_id)
        .values(values)
        .returning(Link.archive_url)
    )
    updated = result.one_or_none()
    if updated is None:
        await session.rollback()
        raise NotFoundError(f"Link {link_id} not found")
    check = Check(
        link_id=link_id, status_code=status_code, latency_ms=latency_ms, error=error
    )
    session.add(check)
    await session.commit()
    await session.refresh(check)  # load server-generated checked_at
    return check, updated.archive_url


async def list_for_link(session: AsyncSession, link_id: int) -> Sequence[Check]:
    await links.get(session, link_id)  # 404 for an unknown link
    result = await session.scalars(
        select(Check)
        .where(Check.link_id == link_id)
        .order_by(Check.checked_at.desc(), Check.id.desc())
    )
    return result.all()
