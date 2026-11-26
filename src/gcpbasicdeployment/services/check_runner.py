"""Run checks for stored links: read targets, check them online, save the results.

A database session cannot be shared by concurrent tasks, so only the network part
runs concurrently; reading and saving happen one at a time.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.core.config import get_settings
from gcpbasicdeployment.core.exceptions import NotFoundError
from gcpbasicdeployment.repositories import checks as repo
from gcpbasicdeployment.schemas.check import CheckAllResult, CheckRead, CheckResult
from gcpbasicdeployment.services.checker import Outcome, check_many

logger = logging.getLogger(__name__)


async def _check_online(urls: list[str]) -> list[Outcome]:
    settings = get_settings()
    return await check_many(
        urls,
        timeout=settings.check_timeout_seconds,
        max_concurrency=settings.max_concurrency,
    )


async def _save(session: AsyncSession, link_id: int, outcome: Outcome) -> CheckResult:
    check = await repo.save(
        session,
        link_id,
        last_status=outcome.status,
        status_code=outcome.status_code,
        latency_ms=outcome.latency_ms,
        error=outcome.error,
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
    )


async def check_link(session: AsyncSession, link_id: int) -> CheckResult:
    _, url = await repo.get_target(session, link_id)
    [outcome] = await _check_online([url])
    return await _save(session, link_id, outcome)


async def check_all(session: AsyncSession) -> CheckAllResult:
    targets = await repo.list_targets(session)
    outcomes = await _check_online([url for _, url in targets])

    results: list[CheckResult] = []
    for (link_id, _), outcome in zip(targets, outcomes, strict=True):
        try:
            results.append(await _save(session, link_id, outcome))
        except NotFoundError:
            logger.info("link deleted during check id=%s", link_id)

    statuses = [result.last_status for result in results]
    return CheckAllResult(
        total=len(results),
        alive=statuses.count("alive"),
        dead=statuses.count("dead"),
        blocked=statuses.count("blocked"),
        results=results,
    )
