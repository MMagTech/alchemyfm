from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_admin
from app.services.audiomuse import audiomuse_client

router = APIRouter(
    prefix="/api/admin/audiomuse",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/anchors")
async def list_alchemy_anchors():
    """Saved Song Alchemy anchors from AudioMuse (id + name)."""
    try:
        anchors = await audiomuse_client.list_anchors()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach AudioMuse: {exc}") from exc
    return {"anchors": anchors}


@router.get("/search_tracks")
async def search_tracks(q: str = Query(min_length=1, max_length=200)):
    """Autocomplete track search (same index as AudioMuse Similarity)."""
    try:
        tracks = await audiomuse_client.search_tracks(q.strip())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach AudioMuse: {exc}") from exc
    return {"tracks": tracks}


@router.get("/track")
async def get_track(item_id: str = Query(min_length=1, max_length=100)):
    """Resolve a track id to title/artist for admin forms."""
    try:
        track = await audiomuse_client.get_track(item_id.strip())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach AudioMuse: {exc}") from exc
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    return track
