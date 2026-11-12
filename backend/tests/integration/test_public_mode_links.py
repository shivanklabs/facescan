"""Public mode: links the backend creates must work when the API is mounted only under /api."""

from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from app.connectors.storage.local_filesystem_download import build_local_storage
from app.core.context import AppContext
from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    ORGANISER_EMAIL,
    MemoryEmailSender,
    frames_body,
    guest_client,
    searchable_event,
)

SPA_INDEX = "<html>SPA Index</html>"
TUNNEL = "https://tunnel.example"


def test_backend_links_work_in_public_mode(
    organiser: TestClient, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: magic link and invite link open the SPA; signed photo URLs, the download path
        and the scene preview URL are served under /api (and only there) in public mode.
    Inputs: organiser client, ctx, tmp_path, monkeypatch.
    Outputs: None.
    Side effects: creates an event and a guest, runs a face search and a scene search.
    """
    # Two people: the face search releases person 1's photo; scene search (which never
    # suggests a released photo) then offers person 2's photo as a candidate.
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1), make_png(2, 1)])
    guest = guest_client(ctx, "public_links@example.test", event_id, invite_url)
    frames = frames_body(*[make_png(1, 1, (40 + i, 30)) for i in range(3)])
    searched = guest.post(f"/events/{event_id}/search", content=frames, headers=FRAMES_HEADERS)
    assert searched.json()["release"]
    photo = guest.get(f"/events/{event_id}/gallery").json()["photos"][0]
    scene = guest.post(f"/events/{event_id}/scene-search", json={"query": "person"})

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(SPA_INDEX, encoding="utf-8")
    monkeypatch.setattr("app.main._get_dist_dir", lambda: dist)
    # Same settings as `uv run tasks public`: API (and signed downloads) under <tunnel>/api.
    settings = ctx.settings.model_copy(
        update={"env": "public", "storage_public_base_url": f"{TUNNEL}/api"}
    )
    public = TestClient(
        create_app(settings, ctx=ctx), base_url="https://testserver", cookies=guest.cookies
    )

    def is_spa(path: str) -> bool:
        res = public.get(path)
        return res.status_code == 200 and res.text == SPA_INDEX

    # Magic link and invite link (also the QR code value) are browser pages.
    email = ctx.email
    assert isinstance(email, MemoryEmailSender)
    magic_link = next(link for to, link in email.sent if to == ORGANISER_EMAIL)
    assert urlsplit(magic_link).path == "/auth/callback"
    assert is_spa(urlsplit(magic_link).path)
    invite = urlsplit(invite_url)
    assert invite.path == f"/e/{event_id}"
    assert is_spa(f"{invite.path}?{invite.query}")

    # Signed photo URL: built with the public base, served under /api, never at the root.
    _, bucket, _, object_key = urlsplit(photo["url"]).path.strip("/").split("/")
    signed = build_local_storage(settings).create_signed_download_url(
        bucket, UUID(event_id), object_key
    )
    assert signed.url.startswith(f"{TUNNEL}/api/storage/")
    signed_path = signed.url.removeprefix(TUNNEL)
    served = public.get(signed_path)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert public.get(signed_path.replace("expires=", "expires=1")).status_code == 403
    assert is_spa(signed_path.removeprefix("/api"))

    # Full-resolution download path and scene preview URL: the frontend adds /api.
    download = public.get(f"/api{photo['download_path']}")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("image/")
    preview_url = scene.json()["candidates"][0]["preview_url"]
    preview = public.get(f"/api{preview_url}")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    tampered = f"/api{preview_url}".replace("signature=", "signature=bad_")
    assert public.get(tampered).status_code == 403
    assert is_spa(preview_url)
