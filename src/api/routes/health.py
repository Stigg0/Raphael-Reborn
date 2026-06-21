"""Health and readiness endpoints."""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict:
    """Check that Qdrant is reachable."""
    qdrant = getattr(request.app.state, "qdrant", None)
    qdrant_ok = False
    if qdrant is not None:
        try:
            qdrant.get_collections()
            qdrant_ok = True
        except Exception:
            pass
    return {"status": "ok" if qdrant_ok else "degraded", "qdrant": qdrant_ok}
