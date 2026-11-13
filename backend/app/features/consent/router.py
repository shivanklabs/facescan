"""HTTP routes for the notice, consent and withdrawal."""

from uuid import UUID

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.core.login_sessions import clear_session_cookie
from app.core.request_checks import Ctx, User
from app.features.consent import service
from app.features.consent.schemas import (
    ConsentRecordResponse,
    ConsentRequest,
    MyConsentsResponse,
    NoticeResponse,
    WithdrawResponse,
)

router = APIRouter(tags=["consent"])


@router.get("/consent/notice", response_model=NoticeResponse)
def notice(ctx: Ctx) -> NoticeResponse:
    """Purpose: the current notice text in English and Hindi."""
    n = service.notice_for(ctx)
    return NoticeResponse(version=n.version, draft=n.draft, texts=n.texts)


@router.post("/events/{event_id}/consent", status_code=204)
def give_consent(event_id: UUID, body: ConsentRequest, user: User, ctx: Ctx) -> Response:
    """Purpose: record the notice and the face-search consent for this event."""
    service.grant(ctx, user, event_id, body.notice_version, body.language)
    return Response(status_code=204)


@router.post("/events/{event_id}/consent/withdraw", response_model=WithdrawResponse)
def withdraw(event_id: UUID, user: User, ctx: Ctx) -> JSONResponse:
    """
    Purpose: withdraw consent (POST /events/{id}/consent/withdraw); marks matches 'withdrawn'
        and ends event sessions.
    """
    counts = service.withdraw(ctx, user, event_id)
    response = JSONResponse(WithdrawResponse(**counts).model_dump())
    if user.session_event_id == event_id:
        clear_session_cookie(response, ctx.settings)
    return response


@router.get("/me/consents", response_model=MyConsentsResponse)
def my_consents(user: User, ctx: Ctx) -> MyConsentsResponse:
    """Purpose: the user's consents (privacy settings)."""
    return MyConsentsResponse(
        consents=[
            ConsentRecordResponse(
                event_id=c.event_id,
                event_name=c.event_name,
                purpose=c.purpose,
                notice_version=c.notice_version,
                granted_at=c.granted_at,
                withdrawn_at=c.withdrawn_at,
            )
            for c in service.my_consents(ctx, user)
        ]
    )
