"""Business logic for platform admin health monitoring."""

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.core.services_setup import AppContext
from app.db.event_isolation import ADMIN_SCOPE, event_transaction, scoped_transaction
from app.features.admin.repository import get_event_health_counts, list_events_admin
from app.features.admin.schemas import (
    AdminHealthResponse,
    EventHealthSummary,
    SystemHealthSummary,
)

logger = logging.getLogger(__name__)


def get_system_health(storage_root: str, backup_dir: str | None = None) -> SystemHealthSummary:
    """
    Purpose: Read system-level operational metrics: disk usage and latest backup timestamp.
    Inputs: storage_root (str path to photo storage), backup_dir (optional str path to backups).
    Outputs: SystemHealthSummary containing disk metrics and last backup time.
    Side effects: inspects filesystem disk usage and backup directory.
    """
    storage_path = Path(storage_root).resolve()
    inspect_path = storage_path if storage_path.exists() else Path.cwd()
    usage = shutil.disk_usage(inspect_path)
    disk_free_bytes = int(usage.free)
    disk_total_bytes = int(usage.total)
    disk_used_percent = (
        round(((disk_total_bytes - disk_free_bytes) / disk_total_bytes) * 100.0, 1)
        if disk_total_bytes > 0
        else 0.0
    )

    last_backup_at: datetime | None = None
    backup_path_str = backup_dir or os.environ.get("FACESCAN_BACKUP_DIR")
    if backup_path_str:
        b_path = Path(backup_path_str).resolve()
        if b_path.exists() and b_path.is_dir():
            subdirs = [d for d in b_path.iterdir() if d.is_dir() and d.name.startswith("backup_")]
            if subdirs:
                # Sort newest by directory name (format: backup_YYYYMMDD_HHMMSSZ) or mtime
                subdirs.sort(key=lambda d: d.name, reverse=True)
                latest_dir = subdirs[0]
                manifest_file = latest_dir / "manifest.json"
                if manifest_file.exists() and manifest_file.is_file():
                    try:
                        data = json.loads(manifest_file.read_text(encoding="utf-8"))
                        if "created_at" in data and isinstance(data["created_at"], str):
                            last_backup_at = datetime.fromisoformat(data["created_at"])
                    except Exception as exc:
                        logger.debug("Failed to parse backup manifest: %s", exc)
                if last_backup_at is None:
                    last_backup_at = datetime.fromtimestamp(latest_dir.stat().st_mtime, tz=UTC)

    return SystemHealthSummary(
        disk_free_bytes=disk_free_bytes,
        disk_total_bytes=disk_total_bytes,
        disk_used_percent=disk_used_percent,
        last_backup_at=last_backup_at,
    )


def get_admin_health(ctx: AppContext) -> AdminHealthResponse:
    """
    Purpose: Collect operational health status for all events and system disk/backup metrics.
    Inputs: ctx (AppContext).
    Outputs: AdminHealthResponse with aggregate metrics per event and system stats.
    Side effects: queries events in admin scope and per-event counts in isolated transactions.
    """
    system_health = get_system_health(ctx.settings.storage_root, ctx.settings.backup_dir)

    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        events = list_events_admin(session)

    event_summaries: list[EventHealthSummary] = []
    for ev in events:
        with event_transaction(ctx.sessions, ev.id) as ev_session:
            counts = get_event_health_counts(ev_session, ev.id)

        quarantined = counts["photos_quarantined"]
        approved = counts["photos_approved"]
        indexed = counts["photos_indexed"]
        failed = counts["photos_failed"]
        total = counts["photos_total"]

        target = approved + indexed
        if total == 0 or target == 0:
            progress = 100.0 if ev.status == "searchable" else 0.0
        else:
            progress = round((indexed / target) * 100.0, 1)
        progress = min(100.0, max(0.0, progress))

        event_summaries.append(
            EventHealthSummary(
                event_id=ev.id,
                name=ev.name,
                country=ev.country,
                status=ev.status,
                created_at=ev.created_at,
                photos_quarantined=quarantined,
                photos_approved=approved,
                photos_indexed=indexed,
                photos_failed=failed,
                photos_total=total,
                indexing_progress_pct=progress,
                job_failures=counts["job_failures"],
                pending_claims=counts["pending_claims"],
                not_me_count=counts["not_me_count"],
            )
        )

    return AdminHealthResponse(events=event_summaries, system=system_health)
