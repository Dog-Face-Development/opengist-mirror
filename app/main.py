from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import func
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import settings
from app.db import engine, get_session, init_db
from app.models import AppSettings, SyncRun, SyncedGist, utcnow
from app.scheduler import sync_scheduler
from app.services.sync_service import ensure_settings_row, run_sync

app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _normalize_url_input(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned


def _token_has_inline_credentials(token: str | None) -> bool:
    if not token or ":" not in token:
        return False
    username, password = token.split(":", 1)
    return bool(username.strip() and password.strip())


def _opengist_auth_configured(app_settings: AppSettings) -> bool:
    return bool(
        app_settings.opengist_token
        and (
            (app_settings.opengist_username and app_settings.opengist_username.strip())
            or _token_has_inline_credentials(app_settings.opengist_token)
        )
    )


@app.on_event("startup")
def on_startup() -> None:
    if settings.database_url.startswith("sqlite:///"):
        db_path = settings.database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    init_db()
    sync_scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    sync_scheduler.shutdown()


@app.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    app_settings = ensure_settings_row(session)
    runs = session.exec(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(20)).all()
    mirrored = session.exec(
        select(SyncedGist).order_by(SyncedGist.last_sync_at.desc()).limit(20)
    ).all()
    mirrored_count = session.exec(select(func.count()).select_from(SyncedGist)).one()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_settings": app_settings,
            "runs": runs,
            "mirrored": mirrored,
            "mirrored_count": mirrored_count,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "github_configured": bool(app_settings.github_token),
            "opengist_configured": _opengist_auth_configured(app_settings),
        },
    )


@app.post("/settings")
def save_settings(
    github_token: str = Form(default=""),
    opengist_url: str = Form(default=""),
    opengist_username: str = Form(default=""),
    opengist_token: str = Form(default=""),
    sync_interval_minutes: int = Form(default=60),
    enabled: str | None = Form(default=None),
    session: Session = Depends(get_session),
):
    app_settings = ensure_settings_row(session)

    if github_token.strip():
        app_settings.github_token = github_token.strip()
    if opengist_token.strip():
        app_settings.opengist_token = opengist_token.strip()
    if opengist_url.strip():
        app_settings.opengist_url = _normalize_url_input(opengist_url)
    if opengist_username.strip():
        app_settings.opengist_username = opengist_username.strip()
    app_settings.sync_interval_minutes = max(1, sync_interval_minutes)
    app_settings.enabled = enabled == "on"
    app_settings.updated_at = utcnow()

    session.add(app_settings)
    session.commit()
    sync_scheduler.reload_job()

    params = urlencode({"message": "Settings saved."})
    return RedirectResponse(url=f"/?{params}", status_code=303)


@app.post("/sync")
def manual_sync(session: Session = Depends(get_session)):
    try:
        run = run_sync(session)
        if run.status == "success":
            params = urlencode({"message": f"Sync run #{run.id} completed successfully."})
        elif run.status == "partial_failure":
            params = urlencode({"error": f"Sync run #{run.id} completed with failures."})
        else:
            params = urlencode({"error": f"Sync run #{run.id} failed."})
    except Exception as error:
        params = urlencode({"error": str(error)})
    return RedirectResponse(url=f"/?{params}", status_code=303)


@app.get("/health")
def health() -> dict[str, str | bool]:
    db_ok = True
    try:
        with Session(engine) as session:
            session.exec(select(AppSettings).limit(1)).first()
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database_ok": db_ok,
        "scheduler_running": sync_scheduler.scheduler.running,
    }

