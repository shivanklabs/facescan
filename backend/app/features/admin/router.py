"""HTTP routes for platform admin health monitoring."""

from fastapi import APIRouter

from app.core.request_checks import AdminUser, Ctx
from app.features.admin.schemas import AdminHealthResponse
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
