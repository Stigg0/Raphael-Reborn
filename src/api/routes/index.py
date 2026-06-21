"""Index stats endpoint."""
from fastapi import APIRouter, Depends, Request

from auth.middleware import require_role

router = APIRouter()


@router.get("/stats", dependencies=[Depends(require_role("read", "sync"))])
async def stats(request: Request) -> dict:
    """Return collection sizes and index health."""
    qdrant = request.app.state.qdrant
    result: dict = {"collections": {}}
    try:
        for col in qdrant.get_collections().collections:
            info = qdrant.get_collection(col.name)
            result["collections"][col.name] = {
                "points": info.points_count,
                "indexed_vectors": info.indexed_vectors_count,
                "status": str(info.status),
            }
    except Exception as exc:
        result["error"] = str(exc)
    return result
