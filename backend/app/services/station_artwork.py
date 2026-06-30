"""Store and serve per-station default artwork (fallback when no track cover)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import Request
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.database import Station

ARTWORK_FILENAME = "artwork.jpg"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 800
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})


def station_dir(slug: str) -> Path:
    return Path(settings.stations_root) / slug


def artwork_path(slug: str) -> Path:
    return station_dir(slug) / ARTWORK_FILENAME


def artwork_exists(slug: str) -> bool:
    path = artwork_path(slug)
    return path.is_file() and path.stat().st_size > 0


def delete_artwork(slug: str) -> None:
    path = artwork_path(slug)
    if path.is_file():
        path.unlink(missing_ok=True)


def save_artwork(slug: str, data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Image too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except UnidentifiedImageError as exc:
        raise ValueError("Invalid image file") from exc

    if img.format not in ALLOWED_FORMATS:
        raise ValueError("Unsupported image type (use JPEG, PNG, WebP, or GIF)")

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (15, 20, 25))
        background.paste(rgba, mask=rgba.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    station_dir(slug).mkdir(parents=True, exist_ok=True)
    img.save(artwork_path(slug), "JPEG", quality=85, optimize=True)


def artwork_api_path(slug: str) -> str:
    return f"/api/stations/{slug}/artwork"


def public_artwork_url(station: Station, request: Request | None = None) -> str:
    if artwork_exists(station.slug):
        path = artwork_api_path(station.slug)
        if request is not None:
            return f"{str(request.base_url).rstrip('/')}{path}"
        return path
    return station.artwork_url or ""
