"""POST /links/{id}/check, POST /check-all and GET /links/{id}/checks."""

from fastapi import APIRouter

from gcpbasicdeployment.repositories import checks as repo
from gcpbasicdeployment.routers.links import SessionDep
from gcpbasicdeployment.schemas.check import CheckAllResult, CheckRead, CheckResult
from gcpbasicdeployment.services import check_runner

router = APIRouter(tags=["checks"])


@router.post("/links/{link_id}/check", response_model=CheckResult)
async def check_link(link_id: int, session: SessionDep) -> CheckResult:
    return await check_runner.check_link(session, link_id)


@router.post("/check-all", response_model=CheckAllResult)
async def check_all(session: SessionDep) -> CheckAllResult:
    return await check_runner.check_all(session)


@router.get("/links/{link_id}/checks", response_model=list[CheckRead])
async def list_checks(link_id: int, session: SessionDep) -> list[CheckRead]:
    checks = await repo.list_for_link(session, link_id)
    return [CheckRead.model_validate(check) for check in checks]
