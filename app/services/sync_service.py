from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.clients.github_client import GitHubClient
from app.clients.opengist_client import OpenGistClient
from app.config import settings
from app.models import AppSettings, SyncRun, SyncedGist, utcnow


def ensure_settings_row(session: Session) -> AppSettings:
    app_settings = session.get(AppSettings, 1)
    if app_settings is None:
        app_settings = AppSettings(id=1, sync_interval_minutes=60, enabled=True)
        session.add(app_settings)
        session.commit()
        session.refresh(app_settings)
    return app_settings


def _parse_github_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_has_inline_credentials(token: str | None) -> bool:
    if not token or ":" not in token:
        return False
    username, password = token.split(":", 1)
    return bool(username.strip() and password.strip())


def _build_opengist_payload(gist: dict[str, Any], files: dict[str, dict[str, str]]) -> dict[str, Any]:
    description = (gist.get("description") or "").strip()
    mirror_tag = f"[mirrored-from-github:{gist.get('id', '')}]"
    tagged_description = f"{mirror_tag} {description}".strip()
    is_public = bool(gist.get("public", False))
    visibility = "public" if is_public else "unlisted"
    names = list(files.keys())
    contents = [str((files.get(name) or {}).get("content") or "") for name in names]
    primary_file_name = names[0] if names else "gist"
    return {
        "title": primary_file_name,
        "description": tagged_description,
        "public": is_public,
        "visibility": visibility,
        "private": {"public": 0, "unlisted": 1, "private": 2}[visibility],
        "name": names,
        "content": contents,
        "files": files,
        "source_gist_id": str(gist.get("id") or ""),
    }


def _extract_file_contents(gist: dict[str, Any], github_client: GitHubClient) -> dict[str, dict[str, str]]:
    files_payload: dict[str, dict[str, str]] = {}
    files = gist.get("files", {})
    for file_name, metadata in files.items():
        content = metadata.get("content")
        if content is None and metadata.get("raw_url"):
            content = github_client.fetch_raw_content(metadata["raw_url"])
        files_payload[file_name] = {"content": content or ""}
    return files_payload


def run_sync(session: Session) -> SyncRun:
    app_settings = ensure_settings_row(session)
    if not app_settings.enabled:
        raise RuntimeError("Sync is disabled in settings.")
    if not app_settings.github_token:
        raise RuntimeError("GitHub token is not configured.")
    if not app_settings.opengist_url:
        raise RuntimeError("OpenGist URL is not configured.")
    if not app_settings.opengist_token:
        raise RuntimeError("OpenGist token is not configured.")
    if not app_settings.opengist_username and not _token_has_inline_credentials(app_settings.opengist_token):
        raise RuntimeError(
            "OpenGist username is not configured. Set OpenGist username and password/token, or use username:token."
        )

    github_client = GitHubClient(
        token=app_settings.github_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    opengist_client = OpenGistClient(
        base_url=app_settings.opengist_url,
        username=app_settings.opengist_username,
        token=app_settings.opengist_token,
        timeout_seconds=settings.request_timeout_seconds,
    )

    run = SyncRun(started_at=utcnow(), status="running")
    session.add(run)
    session.commit()
    session.refresh(run)

    errors: list[str] = []

    try:
        summaries = github_client.list_gists()
        run.total_gists = len(summaries)
        session.add(run)
        session.commit()

        for summary in summaries:
            gist_id = summary["id"]
            detailed_gist = github_client.get_gist(gist_id)
            github_updated_at = _normalize_utc(
                _parse_github_timestamp(detailed_gist.get("updated_at"))
            )
            mapping = session.exec(
                select(SyncedGist).where(SyncedGist.github_gist_id == gist_id)
            ).first()
            mapping_updated_at = _normalize_utc(
                mapping.last_github_updated_at if mapping else None
            )

            if (
                mapping
                and mapping.opengist_gist_id
                and mapping.last_status == "updated"
                and mapping_updated_at
                and github_updated_at
                and mapping_updated_at >= github_updated_at
            ):
                continue

            try:
                file_payload = _extract_file_contents(detailed_gist, github_client)
                payload = _build_opengist_payload(detailed_gist, file_payload)

                if mapping and mapping.opengist_gist_id and "/" in mapping.opengist_gist_id:
                    opengist_client.update_gist(mapping.opengist_gist_id, payload)
                    opengist_gist_id = mapping.opengist_gist_id
                    last_status = "updated"
                else:
                    opengist_gist_id = opengist_client.create_gist(payload)
                    last_status = "created"

                if mapping is None:
                    mapping = SyncedGist(github_gist_id=gist_id)
                mapping.opengist_gist_id = opengist_gist_id
                mapping.last_github_updated_at = github_updated_at
                mapping.last_sync_at = utcnow()
                mapping.last_status = last_status
                mapping.last_error = None
                session.add(mapping)
                run.synced_gists += 1
            except Exception as gist_error:
                gist_error_text = f"{gist_error.__class__.__name__}: {gist_error}"
                if mapping is None:
                    mapping = SyncedGist(github_gist_id=gist_id)
                mapping.last_sync_at = utcnow()
                mapping.last_status = "error"
                mapping.last_error = gist_error_text[:4000]
                session.add(mapping)
                run.failed_gists += 1
                errors.append(f"{gist_id}: {gist_error_text}")

                lowered_error = gist_error_text.lower()
                if "invalid credentials" in lowered_error or "authentication failed" in lowered_error:
                    raise RuntimeError(
                        "OpenGist authentication failed. Check OpenGist username and password/token."
                    ) from gist_error

            session.add(run)
            session.commit()

        run.status = "success" if run.failed_gists == 0 else "partial_failure"
        if errors:
            run.notes = "\n".join(errors[:25])
    except Exception as fatal_error:
        run.status = "failed"
        run.notes = f"{fatal_error.__class__.__name__}: {fatal_error}"[:4000]
    finally:
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        session.refresh(run)

    return run

