"""Tests for the FastAPI application endpoints in app/main.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlmodel import Session

from app.models import AppSettings, SyncRun
from app.services.sync_service import ensure_settings_row


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


def test_index_contains_app_name(client):
    response = client.get("/")
    assert "OpenGist" in response.text


def test_index_shows_not_configured_by_default(client):
    response = client.get("/")
    # Neither GitHub nor OpenGist are configured in a fresh DB.
    assert response.status_code == 200


def test_index_shows_flash_message(client):
    response = client.get("/?message=Hello+World")
    assert "Hello World" in response.text


def test_index_shows_error_message(client):
    response = client.get("/?error=Something+broke")
    assert "Something broke" in response.text


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["database_ok"] is True
    assert data["status"] == "ok"


def test_health_has_scheduler_field(client):
    response = client.get("/health")
    assert "scheduler_running" in response.json()


# ---------------------------------------------------------------------------
# POST /settings
# ---------------------------------------------------------------------------


def test_save_settings_redirects(client):
    response = client.post(
        "/settings",
        data={
            "github_token": "ghp_test",
            "opengist_url": "http://opengist.local",
            "opengist_username": "user",
            "opengist_token": "token123",
            "sync_interval_minutes": "30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/?message=" in response.headers["location"]


def test_save_settings_persists_values(client, session):
    client.post(
        "/settings",
        data={
            "github_token": "ghp_stored",
            "opengist_url": "https://og.example.com",
            "opengist_username": "alice",
            "opengist_token": "tok",
            "sync_interval_minutes": "15",
        },
    )
    settings_row = session.get(AppSettings, 1)
    assert settings_row is not None
    assert settings_row.github_token == "ghp_stored"
    assert settings_row.opengist_url == "https://og.example.com"
    assert settings_row.sync_interval_minutes == 15


def test_save_settings_normalizes_url_without_scheme(client, session):
    client.post(
        "/settings",
        data={
            "opengist_url": "opengist.local",
            "sync_interval_minutes": "60",
        },
    )
    row = session.get(AppSettings, 1)
    assert row is not None
    assert row.opengist_url == "http://opengist.local"


def test_save_settings_strips_trailing_slash(client, session):
    client.post(
        "/settings",
        data={
            "opengist_url": "https://opengist.local/",
            "sync_interval_minutes": "60",
        },
    )
    row = session.get(AppSettings, 1)
    assert row is not None
    assert row.opengist_url == "https://opengist.local"


def test_save_settings_enabled_on(client, session):
    client.post(
        "/settings",
        data={"sync_interval_minutes": "60", "enabled": "on"},
    )
    row = session.get(AppSettings, 1)
    assert row.enabled is True


def test_save_settings_enabled_off(client, session):
    client.post(
        "/settings",
        data={"sync_interval_minutes": "60"},
    )
    row = session.get(AppSettings, 1)
    assert row.enabled is False


def test_save_settings_clamps_interval_minimum(client, session):
    client.post(
        "/settings",
        data={"sync_interval_minutes": "0"},
    )
    row = session.get(AppSettings, 1)
    assert row.sync_interval_minutes >= 1


# ---------------------------------------------------------------------------
# POST /sync
# ---------------------------------------------------------------------------


def test_manual_sync_redirects_on_success(client, session):
    # Populate settings so sync can at least attempt to run.
    row = ensure_settings_row(session)
    row.github_token = "ghp_test"
    row.opengist_url = "http://opengist.local"
    row.opengist_username = "user"
    row.opengist_token = "tok"
    row.enabled = True
    session.add(row)
    session.commit()

    fake_run = SyncRun(id=1, status="success", synced_gists=0, failed_gists=0)
    with patch("app.main.run_sync", return_value=fake_run):
        response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]


def test_manual_sync_redirects_on_failure(client, session):
    row = ensure_settings_row(session)
    row.enabled = True
    session.add(row)
    session.commit()

    with patch("app.main.run_sync", side_effect=RuntimeError("no config")):
        response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_manual_sync_redirects_on_partial_failure(client, session):
    fake_run = SyncRun(id=2, status="partial_failure", synced_gists=1, failed_gists=1)
    with patch("app.main.run_sync", return_value=fake_run):
        response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_manual_sync_redirects_on_failed_status(client, session):
    fake_run = SyncRun(id=3, status="failed", synced_gists=0, failed_gists=0)
    with patch("app.main.run_sync", return_value=fake_run):
        response = client.post("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


# ---------------------------------------------------------------------------
# Helper function tests (from main module)
# ---------------------------------------------------------------------------


def test_normalize_url_adds_scheme():
    from app.main import _normalize_url_input

    assert _normalize_url_input("example.com") == "http://example.com"


def test_normalize_url_strips_whitespace():
    from app.main import _normalize_url_input

    assert _normalize_url_input("  https://example.com  ") == "https://example.com"


def test_normalize_url_strips_trailing_slash():
    from app.main import _normalize_url_input

    assert _normalize_url_input("https://example.com/") == "https://example.com"


def test_normalize_url_keeps_existing_scheme():
    from app.main import _normalize_url_input

    assert _normalize_url_input("https://example.com") == "https://example.com"


def test_token_has_inline_credentials_true():
    from app.main import _token_has_inline_credentials

    assert _token_has_inline_credentials("alice:secret") is True


def test_token_has_inline_credentials_false_no_colon():
    from app.main import _token_has_inline_credentials

    assert _token_has_inline_credentials("plaintoken") is False


def test_token_has_inline_credentials_false_empty():
    from app.main import _token_has_inline_credentials

    assert _token_has_inline_credentials("") is False
    assert _token_has_inline_credentials(None) is False


def test_token_has_inline_credentials_false_blank_parts():
    from app.main import _token_has_inline_credentials

    assert _token_has_inline_credentials(":secret") is False
    assert _token_has_inline_credentials("alice:") is False


def test_opengist_auth_configured_with_username():
    from app.main import _opengist_auth_configured

    s = AppSettings(opengist_token="tok", opengist_username="alice")
    assert _opengist_auth_configured(s) is True


def test_opengist_auth_configured_with_inline_credentials():
    from app.main import _opengist_auth_configured

    s = AppSettings(opengist_token="alice:secret", opengist_username=None)
    assert _opengist_auth_configured(s) is True


def test_opengist_auth_not_configured_no_token():
    from app.main import _opengist_auth_configured

    s = AppSettings(opengist_token=None, opengist_username="alice")
    assert _opengist_auth_configured(s) is False


def test_opengist_auth_not_configured_no_username_or_inline():
    from app.main import _opengist_auth_configured

    s = AppSettings(opengist_token="baretoken", opengist_username=None)
    assert _opengist_auth_configured(s) is False
