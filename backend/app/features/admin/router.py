"""HTTP routes for platform admin health monitoring."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.request_checks import AdminUser, Ctx
from app.features.admin import service
from app.features.admin.schemas import (
    AccountItem,
    AccountList,
    AccountStatusChange,
    AdminHealthResponse,
)
from app.features.admin.service import get_admin_health

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", response_model=AdminHealthResponse)
def get_health(_admin: AdminUser, ctx: Ctx) -> AdminHealthResponse:
    """
    Purpose: Retrieve operational health metrics across all events and host system.
    Inputs: _admin (AdminUser: platform admin with a passkey session),
        ctx (AppContext).
    Outputs: AdminHealthResponse containing event summaries and system stats.
    Side effects: none.
    """
    return get_admin_health(ctx)


def _item(view: service.AccountView) -> AccountItem:
    """Purpose: API shape of one account. Side effects: none."""
    return AccountItem(
        user_id=view.user_id,
        display_name=view.display_name,
        email=view.email,
        role=view.role,
        status=view.status,
        created_at=view.created_at,
    )


@router.get("/accounts", response_model=AccountList)
def list_accounts(
    _admin: AdminUser,
    ctx: Ctx,
    status: Literal["active", "pending", "rejected", "suspended"] | None = Query(default="pending"),
    role: Literal["guest", "organiser", "photographer", "admin"] | None = Query(default=None),
) -> AccountList:
    """
    Purpose: accounts for approvals (default: pending organisers and photographers).
    Inputs: admin, optional status / role filters. Outputs: AccountList (newest first).
    """
    return AccountList(accounts=[_item(v) for v in service.list_accounts(ctx, status, role)])


@router.post("/accounts/{user_id}/status", response_model=AccountItem)
def change_account_status(
    user_id: UUID, body: AccountStatusChange, admin: AdminUser, ctx: Ctx
) -> AccountItem:
    """
    Purpose: approve, reject or suspend an account (never an admin or yourself).
    Inputs: target id, new status. Outputs: the updated account (no email).
    """
    return _item(service.set_account_status(ctx, admin, user_id, body.status))
