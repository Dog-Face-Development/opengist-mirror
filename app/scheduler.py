from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.db import engine
from app.services.sync_service import ensure_settings_row, run_sync

logger = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(daemon=True)
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        self.reload_job()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload_job(self) -> None:
        with self._lock:
            with Session(engine) as session:
                app_settings = ensure_settings_row(session)
                interval = max(1, int(app_settings.sync_interval_minutes))

            if self.scheduler.get_job("scheduled-sync") is not None:
                self.scheduler.remove_job("scheduled-sync")

            self.scheduler.add_job(
                self._scheduled_sync_job,
                trigger="interval",
                minutes=interval,
                id="scheduled-sync",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

    def _scheduled_sync_job(self) -> None:
        try:
            with Session(engine) as session:
                app_settings = ensure_settings_row(session)
                if not app_settings.enabled:
                    return
                run_sync(session)
        except Exception:
            logger.exception("Scheduled sync failed.")


sync_scheduler = SyncScheduler()

