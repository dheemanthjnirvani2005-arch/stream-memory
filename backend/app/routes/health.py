from fastapi import APIRouter
from pydantic import BaseModel

from app.services.health import get_health_status

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status, database_ok = get_health_status()
    return HealthResponse(status=status, database="ok" if database_ok else "unavailable")
