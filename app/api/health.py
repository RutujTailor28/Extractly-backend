from fastapi import APIRouter

from app.utils.db_status import check_database_connection

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return service health and Supabase connectivity status."""
    database_status = check_database_connection()
    status = "healthy" if database_status == "connected" else "degraded"

    return {
        "status": status,
        "service": "extractly-api",
        "database": database_status,
    }
