"""Integration tests for Milestone 6 Part 3:
1. Item 8: QR Code license and invite link generation.
2. Item 9: Configurable age policy (minors_self_search allowed vs restricted).
3. Item 10: Non-destructive consent withdrawal (hide, keep in DB, restore on re-grant).
"""

import uuid
from pathlib import Path

import pytest
from app.core.country_rules import CountryPolicy
from app.core.services_setup import AppContext
from app.db.event_isolation import event_transaction
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    guest_client,
    new_client,
    searchable_event,
)
from tests.integration.test_guest_flow import PERSON_1, PERSON_2, selfie_frames


def test_qr_code_invite_and_license() -> None:
    """
    Purpose: verify that qrcode.react is listed in THIRD_PARTY_LICENSES.md.
    Inputs: None.
    Outputs: None.
    Side effects: reads license file.
    """
    root = Path(__file__).resolve().parents[3]
    lic_file = root / "THIRD_PARTY_LICENSES.md"
    assert lic_file.exists(), "THIRD_PARTY_LICENSES.md must exist"
    content = lic_file.read_text(encoding="utf-8")
    assert "qrcode.react" in content, "qrcode.react must be listed in licenses"


def test_age_policy_allows_minors_and_blocks_when_restricted(
    organiser: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify that minors_self_search: allowed permits minor guests to search,
             and switching to restricted forbids minor searches without code change.
    Inputs: organiser, ctx, monkeypatch.
    Outputs: None.
    Side effects: creates event, searches faces.
    """
    # 1. Under default config (minors_self_search: allowed)
    event_id, invite, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    minor = guest_client(ctx, "minor_allowed@example.test", event_id, invite, adult=False)
    resp = minor.post(
        f"/events/{event_id}/search", content=selfie_frames(1), headers=FRAMES_HEADERS
    )
    assert resp.status_code == 200, f"Expected 200 with minors allowed, got {resp.text}"

    # 2. Switch policy to restricted
    from app.core import country_rules as policy_module
    from app.features.consent import service as consent_service
    from app.features.face_matching import service as matching_service
    from app.features.scene_search import service as scene_service

    restricted_policy = CountryPolicy(
        country="IN", minor_age=18, minors_self_search="restricted", search_window_days=30
    )
    monkeypatch.setattr(
        policy_module, "load_country_policy", lambda *args, **kwargs: restricted_policy
    )
    monkeypatch.setattr(
        consent_service, "load_country_policy", lambda *args, **kwargs: restricted_policy
    )
    monkeypatch.setattr(
        matching_service, "load_country_policy", lambda *args, **kwargs: restricted_policy
    )
    monkeypatch.setattr(
        scene_service, "load_country_policy", lambda *args, **kwargs: restricted_policy
    )

    restricted_minor = guest_client(
        ctx, "minor_blocked@example.test", event_id, invite, adult=False, consent=False
    )
    resp2 = restricted_minor.post(
        f"/events/{event_id}/search", content=selfie_frames(1), headers=FRAMES_HEADERS
    )
    assert resp2.status_code == 403
    assert resp2.json()["detail"] == "minor_guardian_consent_required"


def test_non_destructive_withdrawal_and_restoration(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify consent withdrawal marks matches 'withdrawn' without deleting any data,
             revokes sessions, hides gallery, and restoring consent brings matches back.
    Inputs: organiser, ctx.
    Outputs: None.
    Side effects: creates event, matches face, withdraws consent, re-grants consent.
    """
    event_id, invite, photos = searchable_event(ctx, organiser, PERSON_1 + PERSON_2)
    guest = guest_client(ctx, "withdraw_test@example.test", event_id, invite)

    # Search to populate matches
    resp = guest.post(
        f"/events/{event_id}/search", content=selfie_frames(1), headers=FRAMES_HEADERS
    )
    assert resp.status_code == 200
    assert len(resp.json()["release"]) == 4

    # Verify matches in DB have score_band 'release'
    event_uuid = uuid.UUID(event_id)
    with event_transaction(ctx.sessions, event_uuid) as s:
        release_count = s.execute(
            text("SELECT count(*) FROM matches WHERE score_band = 'release'")
        ).scalar()
        assert release_count == 4

    from tests.helpers import passkey_sign_in, register_passkey

    # Guest registers passkey on phone
    authenticator = register_passkey(guest)

    # Withdraw consent
    w_resp = guest.post(f"/events/{event_id}/consent/withdraw")
    assert w_resp.status_code == 200
    assert w_resp.json()["matches"] == 4
    assert w_resp.json()["sessions"] >= 1

    # Invariant: NO DATA LOSS. Match rows are NOT deleted, but marked 'withdrawn'
    with event_transaction(ctx.sessions, event_uuid) as s:
        total_matches = s.execute(text("SELECT count(*) FROM matches")).scalar()
        assert total_matches == 4, "Matches must NOT be deleted from database"
        withdrawn_count = s.execute(
            text("SELECT count(*) FROM matches WHERE score_band = 'withdrawn'")
        ).scalar()
        assert withdrawn_count == 4
        release_count_now = s.execute(
            text("SELECT count(*) FROM matches WHERE score_band = 'release'")
        ).scalar()
        assert release_count_now == 0

    # Old session is revoked
    assert guest.get(f"/events/{event_id}/gallery").status_code == 401

    # New sign-in without active consent is blocked
    new_sess = new_client(ctx)
    passkey_sign_in(new_sess, authenticator)
    assert new_sess.get(f"/events/{event_id}/gallery").status_code == 403
    assert (
        new_sess.post(
            f"/events/{event_id}/search", content=selfie_frames(1), headers=FRAMES_HEADERS
        ).status_code
        == 403
    )

    # Re-granting consent restores matches to 'release'
    notice = new_sess.get("/consent/notice").json()
    reg = new_sess.post(
        f"/events/{event_id}/consent",
        json={"notice_version": notice["version"], "language": "en", "face_search": True},
    )
    assert reg.status_code == 204

    # Matches are restored
    with event_transaction(ctx.sessions, event_uuid) as s:
        restored_count = s.execute(
            text("SELECT count(*) FROM matches WHERE score_band = 'release'")
        ).scalar()
        assert restored_count == 4

    # Gallery is restored
    gal = new_sess.get(f"/events/{event_id}/gallery")
    assert gal.status_code == 200
    assert len(gal.json()["photos"]) == 4
