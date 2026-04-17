"""Tests for app/services/sync_service.py."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models import AppSettings, SyncedGist, SyncRun
from app.services.sync_service import (
    _build_opengist_payload,
    _extract_file_contents,
    _normalize_utc,
    _parse_github_timestamp,
    _token_has_inline_credentials,
    ensure_settings_row,
    run_sync,
)


# ---------------------------------------------------------------------------
# ensure_settings_row
# ---------------------------------------------------------------------------


def test_ensure_settings_row_creates_default(session):
    row = ensure_settings_row(session)
    assert row is not None
    assert row.id == 1
    assert row.enabled is True
    assert row.sync_interval_minutes == 60


def test_ensure_settings_row_idempotent(session):
    row1 = ensure_settings_row(session)
    row2 = ensure_settings_row(session)
    assert row1.id == row2.id


def test_ensure_settings_row_returns_existing(session):
    existing = AppSettings(id=1, sync_interval_minutes=15, enabled=False)
    session.add(existing)
    session.commit()
    row = ensure_settings_row(session)
    assert row.sync_interval_minutes == 15
    assert row.enabled is False


# ---------------------------------------------------------------------------
# _parse_github_timestamp
# ---------------------------------------------------------------------------


def test_parse_github_timestamp_valid():
    dt = _parse_github_timestamp("2024-01-15T12:30:00Z")
    assert dt is not None
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 15


def test_parse_github_timestamp_none():
    assert _parse_github_timestamp(None) is None


def test_parse_github_timestamp_empty():
    assert _parse_github_timestamp("") is None


# ---------------------------------------------------------------------------
# _normalize_utc
# ---------------------------------------------------------------------------


def test_normalize_utc_naive_datetime():
    naive = datetime(2024, 6, 1, 12, 0, 0)
    result = _normalize_utc(naive)
    assert result.tzinfo is not None
    assert result.tzinfo == timezone.utc


def test_normalize_utc_aware_datetime():
    aware = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = _normalize_utc(aware)
    assert result == aware


def test_normalize_utc_none():
    assert _normalize_utc(None) is None


# ---------------------------------------------------------------------------
# _token_has_inline_credentials
# ---------------------------------------------------------------------------


def test_token_has_inline_credentials():
    assert _token_has_inline_credentials("user:pass") is True


def test_token_no_inline_credentials():
    assert _token_has_inline_credentials("nocodeshere") is False


def test_token_has_inline_credentials_empty():
    assert _token_has_inline_credentials("") is False
    assert _token_has_inline_credentials(None) is False


# ---------------------------------------------------------------------------
# _build_opengist_payload
# ---------------------------------------------------------------------------


def test_build_opengist_payload_public_gist():
    gist = {"id": "abc123", "description": "My snippet", "public": True}
    files = {"hello.py": {"content": "print('hi')"}}
    payload = _build_opengist_payload(gist, files)
    assert payload["visibility"] == "public"
    assert payload["public"] is True
    assert "[mirrored-from-github:abc123]" in payload["description"]
    assert "My snippet" in payload["description"]


def test_build_opengist_payload_private_gist():
    gist = {"id": "xyz", "description": "", "public": False}
    files = {"secret.txt": {"content": "shhh"}}
    payload = _build_opengist_payload(gist, files)
    assert payload["visibility"] == "unlisted"
    assert payload["public"] is False


def test_build_opengist_payload_preserves_file_names():
    gist = {"id": "g1", "description": "", "public": True}
    files = {"a.py": {"content": ""}, "b.py": {"content": ""}}
    payload = _build_opengist_payload(gist, files)
    assert set(payload["name"]) == {"a.py", "b.py"}


def test_build_opengist_payload_empty_description():
    gist = {"id": "g2", "description": None, "public": True}
    files = {"f.txt": {"content": "x"}}
    payload = _build_opengist_payload(gist, files)
    assert "[mirrored-from-github:g2]" in payload["description"]


def test_build_opengist_payload_uses_first_file_as_title():
    gist = {"id": "g3", "description": "", "public": True}
    files = {"main.go": {"content": ""}}
    payload = _build_opengist_payload(gist, files)
    assert payload["title"] == "main.go"


# ---------------------------------------------------------------------------
# _extract_file_contents
# ---------------------------------------------------------------------------


def test_extract_file_contents_inline():
    gist = {
        "files": {
            "script.sh": {"content": "#!/bin/bash", "raw_url": None},
        }
    }
    mock_client = MagicMock()
    result = _extract_file_contents(gist, mock_client)
    assert result["script.sh"]["content"] == "#!/bin/bash"
    mock_client.fetch_raw_content.assert_not_called()


def test_extract_file_contents_fetches_raw_url():
    gist = {
        "files": {
            "big.py": {"content": None, "raw_url": "https://example.com/raw"},
        }
    }
    mock_client = MagicMock()
    mock_client.fetch_raw_content.return_value = "# big file"
    result = _extract_file_contents(gist, mock_client)
    assert result["big.py"]["content"] == "# big file"
    mock_client.fetch_raw_content.assert_called_once_with("https://example.com/raw")


def test_extract_file_contents_empty_files():
    gist = {"files": {}}
    mock_client = MagicMock()
    result = _extract_file_contents(gist, mock_client)
    assert result == {}


# ---------------------------------------------------------------------------
# run_sync – error paths (no external calls)
# ---------------------------------------------------------------------------


def test_run_sync_raises_when_disabled(session):
    row = ensure_settings_row(session)
    row.enabled = False
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError, match="disabled"):
        run_sync(session)


def test_run_sync_raises_without_github_token(session):
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = None
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError, match="GitHub token"):
        run_sync(session)


def test_run_sync_raises_without_opengist_url(session):
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = None
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError, match="OpenGist URL"):
        run_sync(session)


def test_run_sync_raises_without_opengist_token(session):
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = "http://og.local"
    row.opengist_token = None
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError, match="OpenGist token"):
        run_sync(session)


def test_run_sync_raises_without_opengist_username(session):
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = "http://og.local"
    row.opengist_token = "baretoken"
    row.opengist_username = None
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError, match="username"):
        run_sync(session)


def test_run_sync_succeeds_with_no_gists(session):
    """run_sync returns a SyncRun with status=success when there are no GitHub gists."""
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = "http://og.local"
    row.opengist_token = "tok"
    row.opengist_username = "alice"
    session.add(row)
    session.commit()

    with (
        patch("app.services.sync_service.GitHubClient") as MockGH,
        patch("app.services.sync_service.OpenGistClient"),
    ):
        MockGH.return_value.list_gists.return_value = []
        run = run_sync(session)

    assert run.status == "success"
    assert run.total_gists == 0
    assert run.synced_gists == 0


def test_run_sync_creates_gist_record(session):
    """run_sync creates a SyncedGist row when a new gist is mirrored."""
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = "http://og.local"
    row.opengist_token = "tok"
    row.opengist_username = "alice"
    session.add(row)
    session.commit()

    fake_gist = {
        "id": "gist1",
        "description": "test",
        "public": True,
        "updated_at": "2024-01-01T00:00:00Z",
        "files": {"file.py": {"content": "x", "raw_url": None}},
    }

    with (
        patch("app.services.sync_service.GitHubClient") as MockGH,
        patch("app.services.sync_service.OpenGistClient") as MockOG,
    ):
        MockGH.return_value.list_gists.return_value = [fake_gist]
        MockGH.return_value.get_gist.return_value = fake_gist
        MockOG.return_value.create_gist.return_value = "alice/file-py"
        run = run_sync(session)

    assert run.status == "success"
    assert run.synced_gists == 1


def test_run_sync_records_failed_gist(session):
    """run_sync records partial_failure when a single gist fails to mirror."""
    row = ensure_settings_row(session)
    row.enabled = True
    row.github_token = "ghp_test"
    row.opengist_url = "http://og.local"
    row.opengist_token = "tok"
    row.opengist_username = "alice"
    session.add(row)
    session.commit()

    fake_gist = {
        "id": "gist2",
        "description": "",
        "public": False,
        "updated_at": "2024-01-01T00:00:00Z",
        "files": {"f.txt": {"content": "y", "raw_url": None}},
    }

    with (
        patch("app.services.sync_service.GitHubClient") as MockGH,
        patch("app.services.sync_service.OpenGistClient") as MockOG,
    ):
        MockGH.return_value.list_gists.return_value = [fake_gist]
        MockGH.return_value.get_gist.return_value = fake_gist
        MockOG.return_value.create_gist.side_effect = RuntimeError("push failed")
        run = run_sync(session)

    assert run.status == "partial_failure"
    assert run.failed_gists == 1
