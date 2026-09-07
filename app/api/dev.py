"""Development-only database diagnostics. Not for production use."""

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.utils.db_status import check_database_connection

router = APIRouter(
    prefix="/api/v1/dev",
    tags=["dev"],
)


@router.get("/database-status")
def database_status() -> dict[str, str]:
    """
    Temporary development endpoint to verify Supabase connectivity.

    Disabled when ENVIRONMENT is not development.
    """
    if settings.ENVIRONMENT.lower() != "development":
        raise HTTPException(
            status_code=404,
            detail="Development endpoint is not available in this environment.",
        )

    database = check_database_connection()
    status = "healthy" if database == "connected" else "degraded"

    return {
        "status": status,
        "service": "extractly-api",
        "database": database,
        "environment": settings.ENVIRONMENT,
        "note": "development-only endpoint",
    }
