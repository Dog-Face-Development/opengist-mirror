from collections.abc import Generator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


engine_kwargs: dict = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_migrations()


def _run_migrations() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    _ensure_app_settings_opengist_username_column()


def _ensure_app_settings_opengist_username_column() -> None:
    with engine.begin() as connection:
        rows = connection.execute(text("PRAGMA table_info(app_settings)")).all()
        columns = {row[1] for row in rows}
        if "opengist_username" in columns:
            return
        connection.execute(
            text("ALTER TABLE app_settings ADD COLUMN opengist_username VARCHAR")
        )

