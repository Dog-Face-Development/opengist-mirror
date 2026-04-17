"""Shared pytest fixtures for the test suite."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# Use an in-memory SQLite database so tests are isolated and fast.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(name="engine")
def engine_fixture():
    """Create a fresh in-memory SQLite engine for each test."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    """Provide a database session connected to the in-memory test engine."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(engine):
    """Return a FastAPI TestClient whose DB session is the in-memory engine.

    The global ``app.db.engine`` (used by /health and the scheduler) is also
    patched to point at the same in-memory engine so those code paths work
    without hitting a real database.
    """

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with (
        patch("app.db.engine", engine),
        patch("app.main.engine", engine),
        patch("app.scheduler.engine", engine),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    app.dependency_overrides.clear()
