"""POST/GET/DELETE /links. HTTP only; SQL lives in repositories."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.db.session import get_session
from gcpbasicdeployment.repositories import links as repo
from gcpbasicdeployment.schemas.link import LinkCreate, LinkRead

router = APIRouter(prefix="/links", tags=["links"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=LinkRead, status_code=status.HTTP_201_CREATED)
async def create_link(payload: LinkCreate, session: SessionDep) -> LinkRead:
    link = await repo.create(session, str(payload.url), payload.label)
    return LinkRead.model_validate(link)


@router.get("", response_model=list[LinkRead])
async def list_links(session: SessionDep) -> list[LinkRead]:
    return [LinkRead.model_validate(link) for link in await repo.list_all(session)]


@router.get("/{link_id}", response_model=LinkRead)
async def get_link(link_id: int, session: SessionDep) -> LinkRead:
    return LinkRead.model_validate(await repo.get(session, link_id))


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: int, session: SessionDep) -> Response:
    await repo.delete(session, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
