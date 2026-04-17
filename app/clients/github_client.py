from __future__ import annotations

import time
from typing import Any

import httpx


class GitHubClient:
    base_url = "https://api.github.com"

    def __init__(self, token: str, timeout_seconds: int = 30) -> None:
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def list_gists(self) -> list[dict[str, Any]]:
        gists: list[dict[str, Any]] = []
        page = 1

        with httpx.Client(timeout=self.timeout_seconds) as client:
            while True:
                response = self._request_with_retry(
                    client,
                    "GET",
                    f"{self.base_url}/gists",
                    params={"per_page": 100, "page": page},
                )
                page_data = response.json()
                if not page_data:
                    break
                gists.extend(page_data)
                if len(page_data) < 100:
                    break
                page += 1

        return gists

    def get_gist(self, gist_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retry(
                client,
                "GET",
                f"{self.base_url}/gists/{gist_id}",
            )
        return response.json()

    def fetch_raw_content(self, raw_url: str) -> str:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retry(client, "GET", raw_url)
        return response.text

    def _request_with_retry(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        attempts = 3
        for attempt in range(attempts):
            response = client.request(method, url, headers=self.headers, params=params)
            if response.status_code == 403:
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining == "0":
                    reset_at = response.headers.get("x-ratelimit-reset", "unknown")
                    raise RuntimeError(
                        f"GitHub API rate limit exceeded. Reset at unix timestamp {reset_at}."
                    )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < attempts - 1:
                    time.sleep(2**attempt)
                    continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"Failed GitHub request after retries: {method} {url}")

