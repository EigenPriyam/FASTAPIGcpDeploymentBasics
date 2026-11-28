"""Run checks for stored links: read, check online, find archived copies, save.

A database session cannot be shared by concurrent tasks, so only the network parts
run concurrently; reading and saving happen one at a time.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.core.config import get_settings
from gcpbasicdeployment.core.exceptions import NotFoundError
from gcpbasicdeployment.repositories import checks as repo
from gcpbasicdeployment.schemas.check import CheckAllResult, CheckRead, CheckResult
from gcpbasicdeployment.services.archive import find_snapshots
from gcpbasicdeployment.services.checker import Outcome, check_many

logger = logging.getLogger(__name__)


async def _check_online(urls: list[str]) -> list[Outcome]:
    settings = get_settings()
    return await check_many(
        urls,
        timeout=settings.check_timeout_seconds,
        max_concurrency=settings.max_concurrency,
    )


async def _find_copies(
    targets: list[repo.Target], outcomes: list[Outcome]
) -> dict[int, str]:
    """Archived copies for dead links that have none yet.

    Blocked links are never sent: they may name internal machines.
    """
    wanted = [
        (target.id, target.url)
        for target, outcome in zip(targets, outcomes, strict=True)
        if outcome.status == "dead" and target.archive_url is None
    ]
    return await find_snapshots(wanted, get_settings().check_timeout_seconds)


async def _save(
    session: AsyncSession, link_id: int, outcome: Outcome, snapshot: str | None
) -> CheckResult:
    check, archive_url = await repo.save(
        session,
        link_id,
        last_status=outcome.status,
        status_code=outcome.status_code,
        latency_ms=outcome.latency_ms,
        error=outcome.error,
        new_archive_url=snapshot,
        clear_archive_url=outcome.status == "alive",
    )
    # Link id only: URLs can carry tokens in their query string.
    logger.info(
        "link checked id=%s status=%s code=%s error=%s latency_ms=%s",
        link_id,
        outcome.status,
        outcome.status_code,
        outcome.error,
        outcome.latency_ms,
    )
    return CheckResult(
        link_id=link_id,
        last_status=outcome.status,
        check=CheckRead.model_validate(check),
        archive_url=archive_url,
    )


async def check_link(session: AsyncSession, link_id: int) -> CheckResult:
    target = await repo.get_target(session, link_id)
    [outcome] = await _check_online([target.url])
    copies = await _find_copies([target], [outcome])
    return await _save(session, link_id, outcome, copies.get(link_id))


async def check_all(session: AsyncSession) -> CheckAllResult:
    targets = await repo.list_targets(session)
    outcomes = await _check_online([target.url for target in targets])
    copies = await _find_copies(targets, outcomes)

    results: list[CheckResult] = []
    for target, outcome in zip(targets, outcomes, strict=True):
        try:
            results.append(
                await _save(session, target.id, outcome, copies.get(target.id))
            )
        except NotFoundError:
            logger.info("link deleted during check id=%s", target.id)

    statuses = [result.last_status for result in results]
    return CheckAllResult(
        total=len(results),
        alive=statuses.count("alive"),
        dead=statuses.count("dead"),
        blocked=statuses.count("blocked"),
        results=results,
    )
