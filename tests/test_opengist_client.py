"""Tests for app/clients/opengist_client.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.opengist_client import OpenGistClient


def _make_client(
    base_url: str = "http://opengist.local",
    username: str = "alice",
    token: str = "secret",
) -> OpenGistClient:
    return OpenGistClient(base_url=base_url, username=username, token=token)


# ---------------------------------------------------------------------------
# _normalize_base_url
# ---------------------------------------------------------------------------


def test_normalize_base_url_adds_http():
    result = OpenGistClient._normalize_base_url("opengist.local")
    assert result == "http://opengist.local"


def test_normalize_base_url_strips_trailing_slash():
    result = OpenGistClient._normalize_base_url("http://opengist.local/")
    assert result == "http://opengist.local"


def test_normalize_base_url_keeps_https():
    result = OpenGistClient._normalize_base_url("https://og.example.com")
    assert result == "https://og.example.com"


# ---------------------------------------------------------------------------
# _slugify_identifier
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert OpenGistClient._slugify_identifier("My Script.py") == "my-script-py"


def test_slugify_trims_leading_trailing_dashes():
    assert not OpenGistClient._slugify_identifier("-test-").startswith("-")


def test_slugify_max_length():
    result = OpenGistClient._slugify_identifier("a" * 100)
    assert len(result) <= 32


def test_slugify_empty_returns_gist():
    assert OpenGistClient._slugify_identifier("") == "gist"


def test_slugify_special_chars():
    result = OpenGistClient._slugify_identifier("hello world!@#")
    assert result == "hello-world"


# ---------------------------------------------------------------------------
# _safe_relative_path
# ---------------------------------------------------------------------------


def test_safe_relative_path_simple():
    path = OpenGistClient._safe_relative_path("script.py")
    assert path == Path("script.py")


def test_safe_relative_path_rejects_dotdot():
    with pytest.raises(RuntimeError, match="unsafe"):
        OpenGistClient._safe_relative_path("../etc/passwd")


def test_safe_relative_path_strips_leading_slash():
    path = OpenGistClient._safe_relative_path("/rooted.txt")
    assert path == Path("rooted.txt")


def test_safe_relative_path_backslash_normalised():
    path = OpenGistClient._safe_relative_path("dir\\file.py")
    assert path == Path("dir/file.py")


# ---------------------------------------------------------------------------
# _looks_like_identifier_conflict
# ---------------------------------------------------------------------------


def test_looks_like_conflict_non_fast_forward():
    assert OpenGistClient._looks_like_identifier_conflict("non-fast-forward update") is True


def test_looks_like_conflict_already_exists():
    assert OpenGistClient._looks_like_identifier_conflict("already exists on the server") is True


def test_looks_like_conflict_false():
    assert OpenGistClient._looks_like_identifier_conflict("permission denied") is False


# ---------------------------------------------------------------------------
# _preferred_identifier_name
# ---------------------------------------------------------------------------


def test_preferred_identifier_name_from_name_list():
    payload = {"name": ["hello.py", "world.py"]}
    files = {}
    assert OpenGistClient._preferred_identifier_name(payload, files) == "hello.py"


def test_preferred_identifier_name_from_files():
    payload = {}
    files = {"script.sh": {}}
    assert OpenGistClient._preferred_identifier_name(payload, files) == "script.sh"


def test_preferred_identifier_name_fallback_title():
    payload = {"title": "My Gist"}
    files = {}
    assert OpenGistClient._preferred_identifier_name(payload, files) == "My Gist"


def test_preferred_identifier_name_fallback_gist():
    assert OpenGistClient._preferred_identifier_name({}, {}) == "gist"


# ---------------------------------------------------------------------------
# _payload_files
# ---------------------------------------------------------------------------


def test_payload_files_returns_files_dict():
    payload = {"files": {"a.py": {"content": "x"}}}
    assert OpenGistClient._payload_files(payload) == {"a.py": {"content": "x"}}


def test_payload_files_raises_on_empty():
    with pytest.raises(RuntimeError, match="no files"):
        OpenGistClient._payload_files({"files": {}})


def test_payload_files_raises_on_missing():
    with pytest.raises(RuntimeError, match="no files"):
        OpenGistClient._payload_files({})


# ---------------------------------------------------------------------------
# _credential_parts
# ---------------------------------------------------------------------------


def test_credential_parts_inline_token():
    c = _make_client(token="bob:mypassword", username="")
    user, pwd = c._credential_parts()
    assert user == "bob"
    assert pwd == "mypassword"


def test_credential_parts_separate_username():
    c = _make_client(username="alice", token="mytoken")
    user, pwd = c._credential_parts()
    assert user == "alice"
    assert pwd == "mytoken"


def test_credential_parts_bearer_prefix():
    c = _make_client(username="alice", token="Bearer mytoken")
    _, pwd = c._credential_parts()
    assert pwd == "mytoken"


def test_credential_parts_raises_no_username():
    c = _make_client(username="", token="baretoken")
    with pytest.raises(RuntimeError, match="username"):
        c._credential_parts()


# ---------------------------------------------------------------------------
# _normalize_gist_path
# ---------------------------------------------------------------------------


def test_normalize_gist_path_plain():
    c = _make_client()
    assert c._normalize_gist_path("alice/mygist") == "alice/mygist"


def test_normalize_gist_path_strips_git_suffix():
    c = _make_client()
    assert c._normalize_gist_path("alice/mygist.git") == "alice/mygist"


def test_normalize_gist_path_full_url():
    c = _make_client()
    assert c._normalize_gist_path("http://opengist.local/alice/mygist") == "alice/mygist"


def test_normalize_gist_path_raises_empty():
    c = _make_client()
    with pytest.raises(RuntimeError, match="empty"):
        c._normalize_gist_path("")


# ---------------------------------------------------------------------------
# _build_authenticated_url
# ---------------------------------------------------------------------------


def test_build_authenticated_url_includes_credentials():
    c = _make_client(base_url="http://opengist.local", username="alice", token="secret")
    url = c._build_authenticated_url("alice/mygist")
    assert "alice" in url
    assert "secret" in url
    assert "opengist.local" in url


def test_build_authenticated_url_raises_invalid_base():
    c = _make_client(base_url="not-a-url")
    # After normalization, "not-a-url" becomes "http://not-a-url" which is valid.
    # Use an obviously malformed URL instead.
    c.base_url = "://bad"
    with pytest.raises(RuntimeError, match="Invalid OpenGist URL"):
        c._build_authenticated_url("path")


# ---------------------------------------------------------------------------
# _extract_created_gist_path
# ---------------------------------------------------------------------------


def test_extract_created_gist_path_from_marker():
    c = _make_client()
    output = "remote: Your new repository has been created here: http://opengist.local/alice/mygist"
    result = c._extract_created_gist_path(output)
    assert result == "alice/mygist"


def test_extract_created_gist_path_from_url_in_output():
    c = _make_client()
    output = "Enumerating objects: 3, done.\nTo http://opengist.local/alice/snippet\n * [new branch] master -> master"
    result = c._extract_created_gist_path(output)
    assert result == "alice/snippet"


def test_extract_created_gist_path_returns_none_on_no_match():
    c = _make_client()
    result = c._extract_created_gist_path("nothing useful here")
    assert result is None
