"""Integration tests for Milestone 6 Part 4:
1. Item 13: Admin health page (/admin/health) with role enforcement via FACESCAN_AUTH_ADMIN_EMAILS.
2. Invariant verification: Counts only, no photos, faces, or personal data.
3. System metrics: disk space and backup timestamp.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.background_jobs.models import Job
from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.features.events.models import EventMember
from app.features.face_matching.models import CannotLink
from app.features.gallery.models import Claim
from app.features.photo_upload.models import Photo
from fastapi.testclient import TestClient
from sqlalchemy import insert
from tests.conftest import make_png
from tests.helpers import magic_sign_in, new_client, searchable_event, staff_client


def test_admin_auth_and_permissions(ctx: AppContext, monkeypatch) -> None:
    """
    Purpose: Verify authentication and authorization rules for /admin/health:
             - 401 for unauthenticated requests
             - 403 (passkey_required) for magic-link-only sessions
             - 403 (admin_required) for staff not in FACESCAN_AUTH_ADMIN_EMAILS
             - 200 for passkey staff listed in FACESCAN_AUTH_ADMIN_EMAILS
    Inputs: ctx (AppContext), monkeypatch.
    Outputs: None.
    Side effects: tests HTTP endpoints.
    """
    # 1. Unauthenticated request -> 401
    client = new_client(ctx)
    resp = client.get("/admin/health")
    assert resp.status_code == 401, f"Expected 401 unauthenticated, got {resp.status_code}"

    # 2. Magic-link session without passkey -> 403 passkey_required
    staff_email = "staff_no_passkey@example.test"
    monkeypatch.setattr(
        ctx.settings,
        "auth_organiser_emails",
        f"{ctx.settings.auth_organiser_emails},{staff_email},regular_organiser@example.test",
    )
    magic_sign_in(client, ctx, staff_email)
    resp = client.get("/admin/health")
    assert resp.status_code == 403, f"Expected 403 passkey_required, got {resp.status_code}"

    # 3. Staff with passkey, but NOT on admin list -> 403 admin_required
    organiser_client = staff_client(ctx, "regular_organiser@example.test")
    resp = organiser_client.get("/admin/health")
    assert resp.status_code == 403, f"Expected 403 admin_required, got {resp.status_code}"
    assert resp.json().get("detail") == "admin_required"

    # 4. Staff with passkey AND on admin list -> 200 OK
    admin_email = "superadmin@example.test"
    monkeypatch.setattr(ctx.settings, "auth_admin_emails", f"{admin_email},other@example.test")
    admin = staff_client(ctx, admin_email)
    resp = admin.get("/admin/health")
    assert resp.status_code == 200, f"Expected 200 OK for admin, got {resp.text}"
    data = resp.json()
    assert "events" in data
    assert "system" in data


def test_admin_health_metrics_and_counts(
    ctx: AppContext, organiser: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """
    Purpose: Verify aggregate counts per event and host system disk/backup metrics.
    Inputs: ctx, organiser, tmp_path, monkeypatch.
    Outputs: None.
    Side effects: creates mock backup, inserts event data and queries /admin/health.
    """
    admin_email = "admin_metrics@example.test"
    monkeypatch.setattr(ctx.settings, "auth_admin_emails", admin_email)
    admin = staff_client(ctx, admin_email)

    # Setup mock backup directory
    backup_root = tmp_path / "backups"
    backup_dir = backup_root / "backup_20261111_120000Z"
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": "2026-11-11T12:00:00+00:00",
        "file_count": 5,
        "total_bytes": 10240,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(ctx.settings, "backup_dir", str(backup_root))

    # Create searchable event
    event_id_str, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1), make_png(2, 2)])
    event_id = UUID(event_id_str)

    # Insert a failed job, a pending claim, and a cannot-link record
    with event_transaction(ctx.sessions, event_id) as session:
        # Failed job
        session.execute(
            insert(Job).values(
                event_id=event_id,
                type="index_collection",
                status="failed",
                payload={},
                last_error="test failure",
            )
        )
        photo = session.query(Photo).filter_by(event_id=event_id).first()
        assert photo is not None
        member = session.query(EventMember).filter_by(event_id=event_id).first()
        assert member is not None

        # Claim
        claim = Claim(
            event_id=event_id,
            user_id=member.user_id,
            photo_id=photo.id,
            status="pending",
            reason="face_check_failed",
        )
        session.add(claim)

        # Cannot link
        cannot = CannotLink(
            event_id=event_id,
            user_id=member.user_id,
            photo_id=photo.id,
        )
        session.add(cannot)

    # Query admin health
    resp = admin.get("/admin/health")
    assert resp.status_code == 200
    data = resp.json()

    # System assertions
    system = data["system"]
    assert system["disk_free_bytes"] > 0
    assert system["disk_total_bytes"] > 0
    assert 0.0 <= system["disk_used_percent"] <= 100.0
    assert system["last_backup_at"] is not None
    assert datetime.fromisoformat(system["last_backup_at"]) == datetime(
        2026, 11, 11, 12, 0, 0, tzinfo=UTC
    )

    # Event assertions
    events = data["events"]
    matching = [e for e in events if e["event_id"] == event_id_str]
    assert len(matching) == 1
    ev_stat = matching[0]

    assert ev_stat["photos_total"] >= 2
    assert ev_stat["photos_indexed"] >= 2
    assert ev_stat["indexing_progress_pct"] == 100.0
    assert ev_stat["job_failures"] >= 1
    assert ev_stat["pending_claims"] >= 1
    assert ev_stat["not_me_count"] >= 1


def test_admin_privacy_counts_only_invariant(ctx: AppContext, monkeypatch) -> None:
    """
    Purpose: Verify that /admin/health response strictly contains aggregate numbers,
             with ZERO photos, images, face vectors, or IP addresses.
    Inputs: ctx, monkeypatch.
    Outputs: None.
    Side effects: inspects response payload keys.
    """
    admin_email = "admin_privacy@example.test"
    monkeypatch.setattr(ctx.settings, "auth_admin_emails", admin_email)
    admin = staff_client(ctx, admin_email)

    resp = admin.get("/admin/health")
    assert resp.status_code == 200
    raw_text = resp.text.lower()

    # Disallowed sensitive terms
    forbidden = [
        "embedding",
        "bounding_box",
        "object_key",
        "bucket",
        "face_id",
        "photo_url",
        "ip_address",
    ]
    for word in forbidden:
        assert word not in raw_text, (
            f"Forbidden privacy-violating term '{word}' found in admin health response!"
        )
