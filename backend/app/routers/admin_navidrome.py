import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin
from app.database import SessionLocal
from app.services.broadcast_settings import get_broadcast_settings
from app.services.navidrome import navidrome_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/navidrome",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class NavidromePlaylistRead(BaseModel):
    id: str
    name: str


class NavidromePlaylistsResponse(BaseModel):
    playlists: list[NavidromePlaylistRead]


class HeartRequest(BaseModel):
    hearted: bool


class HeartResponse(BaseModel):
    hearted: bool
    playlist_added: bool | None = None
    playlist_configured: bool = True


@router.get("/playlists", response_model=NavidromePlaylistsResponse)
async def list_playlists():
    try:
        playlists = await navidrome_client.list_playlists()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list Navidrome playlists")
        raise HTTPException(status_code=502, detail="Could not load Navidrome playlists") from exc
    return NavidromePlaylistsResponse(
        playlists=[NavidromePlaylistRead(id=p.id, name=p.name) for p in playlists]
    )


@router.post("/songs/{item_id}/heart", response_model=HeartResponse)
async def set_song_heart(item_id: str, payload: HeartRequest):
    # Short-lived session for the settings read only -- released before the
    # Navidrome awaits below rather than held open for their duration (same
    # class of bug fixed for /listen and /api/stations/{slug}).
    db = SessionLocal()
    try:
        playlist_id = (get_broadcast_settings(db).default_navidrome_playlist_id or "").strip()
    finally:
        db.close()

    try:
        if payload.hearted:
            playlist_added = await navidrome_client.heart_song_with_playlist(
                item_id, playlist_id or None
            )
            return HeartResponse(
                hearted=True,
                playlist_added=playlist_added if playlist_id else None,
                playlist_configured=bool(playlist_id),
            )
        await navidrome_client.unstar_song(item_id)
        return HeartResponse(hearted=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update heart for %s", item_id)
        raise HTTPException(status_code=502, detail="Navidrome heart action failed") from exc
