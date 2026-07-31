"""Application health endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def get_health() -> dict[str, str]:
    """Report that the application bootstrap is ready."""

    return {"status": "ok", "version": "0.1.0"}
