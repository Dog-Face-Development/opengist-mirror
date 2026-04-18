"""Tests for app/clients/github_client.py."""
from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from app.clients.github_client import GitHubClient


@pytest.fixture()
def client():
    return GitHubClient(token="ghp_test", timeout_seconds=10)


# ---------------------------------------------------------------------------
# list_gists
# ---------------------------------------------------------------------------


def test_list_gists_empty(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=1",
        json=[],
    )
    gists = client.list_gists()
    assert gists == []


def test_list_gists_single_page(client, httpx_mock: HTTPXMock):
    gist_data = [{"id": "abc", "description": "hello"}]
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=1",
        json=gist_data,
    )
    gists = client.list_gists()
    assert len(gists) == 1
    assert gists[0]["id"] == "abc"


def test_list_gists_multiple_pages(client, httpx_mock: HTTPXMock):
    page1 = [{"id": str(i)} for i in range(100)]
    page2 = [{"id": "extra"}]
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=1",
        json=page1,
    )
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=2",
        json=page2,
    )
    gists = client.list_gists()
    assert len(gists) == 101


def test_list_gists_sends_auth_header(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=1",
        json=[],
    )
    client.list_gists()
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer ghp_test"


# ---------------------------------------------------------------------------
# get_gist
# ---------------------------------------------------------------------------


def test_get_gist_returns_json(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.github.com/gists/abc123",
        json={"id": "abc123", "files": {}},
    )
    result = client.get_gist("abc123")
    assert result["id"] == "abc123"


# ---------------------------------------------------------------------------
# fetch_raw_content
# ---------------------------------------------------------------------------


def test_fetch_raw_content(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/content",
        text="raw file content",
    )
    result = client.fetch_raw_content("https://raw.githubusercontent.com/content")
    assert result == "raw file content"


# ---------------------------------------------------------------------------
# _request_with_retry – rate limit / server errors
# ---------------------------------------------------------------------------


def test_rate_limit_raises_runtime_error(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.github.com/gists?per_page=100&page=1",
        status_code=403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"},
        json={"message": "rate limit exceeded"},
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        client.list_gists()


def test_server_error_retries_and_raises(client, httpx_mock: HTTPXMock):
    # Provide 3 500 responses to exhaust all retry attempts.
    for _ in range(3):
        httpx_mock.add_response(
            url="https://api.github.com/gists?per_page=100&page=1",
            status_code=500,
        )
    with pytest.raises(httpx.HTTPStatusError):
        client.list_gists()


def test_headers_contain_api_version(client):
    assert client.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "application/vnd.github+json" in client.headers["Accept"]
