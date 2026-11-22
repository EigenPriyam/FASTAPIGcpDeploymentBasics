"""Link create / read models."""

from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, UrlConstraints


class LinkCreate(BaseModel):
    url: Annotated[AnyHttpUrl, UrlConstraints(max_length=2048)]
    label: str | None = Field(default=None, max_length=200)


class LinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    label: str | None
    created_at: datetime
    last_status: str | None
    archive_url: str | None
