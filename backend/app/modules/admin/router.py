"""HTTP routes for platform admin health monitoring."""

from fastapi import APIRouter

from app.core.deps import AdminUser, Ctx
from app.modules.admin.schemas import AdminHealthResponse
from app.modules.admin.service import get_admin_health

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", response_model=AdminHealthResponse)
def get_health(_admin: AdminUser, ctx: Ctx) -> AdminHealthResponse:
    """
    Purpose: Retrieve operational health metrics across all events and host system.
    Inputs: _admin (AdminUser: validated passkey staff on FACESCAN_AUTH_ADMIN_EMAILS),
        ctx (AppContext).
    Outputs: AdminHealthResponse containing event summaries and system stats.
    Side effects: none.
    """
    return get_admin_health(ctx)
