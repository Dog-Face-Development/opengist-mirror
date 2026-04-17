from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppSettings(SQLModel, table=True):
    __tablename__ = "app_settings"

    id: Optional[int] = Field(default=1, primary_key=True)
    github_token: Optional[str] = Field(default=None)
    opengist_url: Optional[str] = Field(default=None)
    opengist_username: Optional[str] = Field(default=None)
    opengist_token: Optional[str] = Field(default=None)
    sync_interval_minutes: int = Field(default=60)
    enabled: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=utcnow)


class SyncedGist(SQLModel, table=True):
    __tablename__ = "synced_gists"

    id: Optional[int] = Field(default=None, primary_key=True)
    github_gist_id: str = Field(
        sa_column=Column(String, nullable=False, unique=True, index=True)
    )
    opengist_gist_id: Optional[str] = Field(default=None, index=True)
    last_github_updated_at: Optional[datetime] = Field(default=None)
    last_sync_at: datetime = Field(default_factory=utcnow)
    last_status: str = Field(default="created")
    last_error: Optional[str] = Field(default=None)


class SyncRun(SQLModel, table=True):
    __tablename__ = "sync_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = Field(default=None)
    status: str = Field(default="running")
    total_gists: int = Field(default=0)
    synced_gists: int = Field(default=0)
    failed_gists: int = Field(default=0)
    notes: Optional[str] = Field(default=None)

