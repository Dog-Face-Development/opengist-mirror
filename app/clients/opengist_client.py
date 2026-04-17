from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

_REPO_CREATED_MARKER = "Your new repository has been created here:"


class OpenGistClient:
    """OpenGist transport based on Git-over-HTTP.

    OpenGist docs indicate API write endpoints are still evolving, while creating/updating
    snippets through Git pushes is stable. This client mirrors gists by pushing git commits.
    """

    def __init__(
        self,
        base_url: str,
        username: str | None,
        token: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.username = (username or "").strip()
        self.token = token.strip()
        self.timeout_seconds = max(30, timeout_seconds)

    def create_gist(self, payload: dict[str, Any]) -> str:
        files = self._payload_files(payload)
        preferred_name = self._preferred_identifier_name(payload, files)
        source_gist_id = str(payload.get("source_gist_id") or "").strip()
        base_identifier = self._slugify_identifier(preferred_name)
        fallback_identifier = self._slugify_identifier(f"{base_identifier}-{source_gist_id[:8]}")

        candidates: list[str] = [base_identifier]
        if fallback_identifier and fallback_identifier not in candidates:
            candidates.append(fallback_identifier)

        last_error: RuntimeError | None = None
        for identifier in candidates:
            try:
                return self._create_gist_with_identifier(identifier, files)
            except RuntimeError as error:
                last_error = error
                if self._looks_like_identifier_conflict(str(error)):
                    continue
                raise

        with tempfile.TemporaryDirectory(prefix="opengist-mirror-") as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

            self._init_repo(repo_dir)
            self._write_files(repo_dir, files)
            self._commit_if_needed(repo_dir, "Mirror gist creation")

            remote_url = self._build_authenticated_url("init")
            self._run_git(repo_dir, "remote", "add", "origin", remote_url)
            push_output = self._run_git(repo_dir, "push", "-u", "origin", "master")

            gist_path = self._extract_created_gist_path(push_output)
            if not gist_path:
                if last_error is not None:
                    raise last_error
                raise RuntimeError(
                    "OpenGist accepted git push to /init but did not return the created gist URL. "
                    "Check OpenGist server logs and permissions."
                )
            return gist_path

    def update_gist(self, gist_id: str, payload: dict[str, Any]) -> None:
        files = self._payload_files(payload)
        gist_path = self._normalize_gist_path(gist_id)
        remote_url = self._build_authenticated_url(gist_path)

        with tempfile.TemporaryDirectory(prefix="opengist-mirror-") as tmp:
            repo_dir = Path(tmp) / "repo"

            cloned = self._try_clone(Path(tmp), remote_url, repo_dir)
            if not cloned:
                repo_dir.mkdir(parents=True, exist_ok=True)
                self._init_repo(repo_dir)
            else:
                self._run_git(repo_dir, "checkout", "-B", "master")

            self._clear_worktree(repo_dir)
            self._write_files(repo_dir, files)
            had_changes = self._commit_if_needed(repo_dir, "Mirror gist update")

            if not had_changes:
                return

            if not cloned:
                self._run_git(repo_dir, "remote", "add", "origin", remote_url)
                self._run_git(repo_dir, "push", "-u", "origin", "master")
            else:
                self._run_git(repo_dir, "push", "origin", "master")

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.strip()
        if "://" not in normalized:
            normalized = f"http://{normalized}"
        return normalized.rstrip("/")

    @staticmethod
    def _payload_files(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
        files = payload.get("files")
        if not isinstance(files, dict) or not files:
            raise RuntimeError("OpenGist sync payload contains no files.")
        return files

    def _normalize_gist_path(self, gist_id: str) -> str:
        value = gist_id.strip()
        if not value:
            raise RuntimeError("OpenGist gist id/path is empty.")
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            value = parsed.path.strip("/")
        return value.removesuffix(".git")

    def _credential_parts(self) -> tuple[str, str]:
        token = self.token
        if not token:
            raise RuntimeError("OpenGist token is empty.")

        if ":" in token:
            username, password = token.split(":", 1)
            if username.strip() and password.strip():
                return username.strip(), password.strip()

        lowered = token.lower()
        if lowered.startswith("token "):
            password = token[6:].strip()
        elif lowered.startswith("bearer "):
            password = token[7:].strip()
        else:
            password = token.strip()

        if not password:
            raise RuntimeError("OpenGist token is empty.")

        if self.username:
            return self.username, password

        raise RuntimeError(
            "OpenGist username is empty. Configure OpenGist username and password/token, or use username:token."
        )

    def _build_authenticated_url(self, path_suffix: str) -> str:
        parsed = urlparse(self.base_url)
        if not parsed.netloc:
            raise RuntimeError(f"Invalid OpenGist URL: {self.base_url}")

        username, password = self._credential_parts()
        auth_netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{parsed.netloc}"
        base_path = parsed.path.rstrip("/")
        suffix = "/" + path_suffix.strip("/") if path_suffix else ""
        return f"{parsed.scheme}://{auth_netloc}{base_path}{suffix}"

    def _create_gist_with_identifier(
        self, identifier: str, files: dict[str, dict[str, str]]
    ) -> str:
        username, _ = self._credential_parts()
        remote_path = f"{username}/{identifier}"

        with tempfile.TemporaryDirectory(prefix="opengist-mirror-") as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

            self._init_repo(repo_dir)
            self._write_files(repo_dir, files)
            self._commit_if_needed(repo_dir, "Mirror gist creation")

            remote_url = self._build_authenticated_url(remote_path)
            self._run_git(repo_dir, "remote", "add", "origin", remote_url)
            self._run_git(repo_dir, "push", "-u", "origin", "master")
            return remote_path

    def _init_repo(self, repo_dir: Path) -> None:
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "checkout", "-B", "master")
        self._run_git(repo_dir, "config", "user.name", "OpenGist Mirror")
        self._run_git(repo_dir, "config", "user.email", "mirror@local")

    def _clear_worktree(self, repo_dir: Path) -> None:
        for entry in repo_dir.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def _write_files(self, repo_dir: Path, files: dict[str, dict[str, str]]) -> None:
        for filename, data in files.items():
            safe_relative = self._safe_relative_path(filename)
            target = repo_dir / safe_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = ""
            if isinstance(data, dict):
                raw = data.get("content")
                content = "" if raw is None else str(raw)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _safe_relative_path(name: str) -> Path:
        normalized = name.replace("\\", "/").lstrip("/")
        path = Path(normalized)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise RuntimeError(f"Refusing unsafe filename path: {name}")
        return path

    @staticmethod
    def _preferred_identifier_name(
        payload: dict[str, Any], files: dict[str, dict[str, str]]
    ) -> str:
        names = payload.get("name")
        if isinstance(names, list) and names and str(names[0]).strip():
            return str(names[0]).strip()
        for file_name in files:
            if file_name.strip():
                return file_name.strip()
        title = str(payload.get("title") or "").strip()
        return title or "gist"

    @staticmethod
    def _slugify_identifier(value: str) -> str:
        lowered = value.lower().strip()
        lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
        lowered = lowered.strip("-")
        if not lowered:
            lowered = "gist"
        return lowered[:32]

    @staticmethod
    def _looks_like_identifier_conflict(message: str) -> bool:
        lowered = message.lower()
        conflict_indicators = (
            "non-fast-forward",
            "fetch first",
            "failed to push some refs",
            "already exists",
        )
        return any(indicator in lowered for indicator in conflict_indicators)

    def _commit_if_needed(self, repo_dir: Path, message: str) -> bool:
        self._run_git(repo_dir, "add", "-A")
        status = self._run_git(repo_dir, "status", "--porcelain")
        if not status.strip():
            return False
        self._run_git(repo_dir, "commit", "-m", message)
        return True

    def _try_clone(self, working_dir: Path, remote_url: str, repo_dir: Path) -> bool:
        command = ["git", "clone", remote_url, str(repo_dir)]
        result = self._run_git_process(working_dir, command)
        if result.returncode == 0:
            return True
        return False

    def _extract_created_gist_path(self, push_output: str) -> str | None:
        lines = [line.strip() for line in push_output.splitlines() if line.strip()]
        for line in lines:
            if _REPO_CREATED_MARKER in line:
                url = line.split(_REPO_CREATED_MARKER, 1)[1].strip()
                parsed = urlparse(url.rstrip("."))
                path = parsed.path.strip("/").removesuffix(".git")
                if path and path != "init":
                    return path

        url_matches = re.findall(r"https?://[^\s]+", push_output)
        for url in url_matches:
            parsed = urlparse(url.rstrip("."))
            path = parsed.path.strip("/").removesuffix(".git")
            if path and path != "init":
                return path
        return None

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _run_git(self, cwd: Path, *args: str) -> str:
        command = ["git", *args]
        result = self._run_git_process(cwd, command)
        output = self._redact_sensitive((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""))

        if result.returncode != 0:
            lowered = output.lower()
            auth_hint = ""
            if "invalid credentials" in lowered or "authentication failed" in lowered:
                auth_hint = (
                    " OpenGist authentication failed. Verify OpenGist username and password/token "
                    "(or use legacy username:token format)."
                )
            raise RuntimeError(
                f"Git command failed ({' '.join(command)}): {output.strip() or '(no output)'}{auth_hint}"
            )
        return output

    def _run_git_process(self, cwd: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=str(cwd),
                env=self._git_env(),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                "Git executable was not found. Install git in the runtime environment."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Git command timed out: {' '.join(command)}") from error

    def _redact_sensitive(self, text: str) -> str:
        if not text:
            return text
        try:
            username, password = self._credential_parts()
        except RuntimeError:
            return text

        parsed = urlparse(self.base_url)
        secret_prefix = f"{parsed.scheme}://{quote(username, safe='')}:{quote(password, safe='')}@"
        return text.replace(secret_prefix, f"{parsed.scheme}://***:***@")

