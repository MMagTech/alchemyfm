import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database import Station
from app.services.broadcast_settings import apply_broadcast_settings, get_broadcast_settings
from app.services.icecast_config import encode_format_liquidsoap

LIQUIDSOAP_DIR = Path(settings.data_dir) / "liquidsoap"
STATIONS_LIQ_DIR = LIQUIDSOAP_DIR / "stations"
MANIFEST_PATH = LIQUIDSOAP_DIR / "manifest.json"

LIQ_HEADER = '''\
# Each station runs as its own process (see supervisor.sh) but they all
# append to this one shared file -- fine for ordinary operational logging
# (each process's own writes stay line-atomic), avoids one log file per
# station cluttering the admin Logs page.
settings.log.file.set(true)
settings.log.file.path.set("/data/logs/liquidsoap.log")

backend_url = "http://backend:8080"
callback_secret = "{callback_secret}"
icecast_host = "icecast"
icecast_port = 8000
source_password = "{source_password}"

def make_station(~slug, ~mount, ~name, ~description) =
  playlist_path = "/data/stations/" ^ slug ^ "/queue.m3u"
  # mode="normal" is load-bearing: playlist() defaults to "randomize", which
  # shuffles the queue.m3u the backend just ordered (harmonic/daypart/journey
  # sequencing) and makes every watch-reload jump to a random position.
  src = playlist(id="pl-" ^ slug, mode="normal", reload_mode="watch", playlist_path)
{crossfade_line}  def on_start(m) =
    artist = m["artist"] ?? ""
    title = m["title"] ?? ""
    uri = backend_url ^ "/internal/stations/" ^ slug ^ "/track-started?secret=" ^ url.encode(callback_secret) ^ "&artist=" ^ url.encode(artist) ^ "&title=" ^ url.encode(title)
    ignore(http.get(normalize_url=false, uri))
  end
  src.on_track(on_start)
  output.icecast(
    {encode_format},
    host=icecast_host,
    port=icecast_port,
    password=source_password,
    mount=mount,
    name=name,
    description=description,
    genre="{genre}",
    public=false,
    src
  )
  log.info(label="station", "Started station " ^ slug ^ " on mount " ^ mount)
end

'''


def _liq_credentials() -> tuple[str, str]:
    return (
        settings.liquidsoap_callback_secret.replace('"', '\\"'),
        settings.icecast_source_password.replace('"', '\\"'),
    )


def _crossfade_line(crossfade_sec: int) -> str:
    if crossfade_sec <= 0:
        return "  src = mksafe(src)\n"
    d = crossfade_sec
    return f"  src = mksafe(crossfade(duration={d}., mksafe(src)))\n"


def _station_script(station: Station, db: Session) -> str:
    callback_secret, source_password = _liq_credentials()
    bs = get_broadcast_settings(db)
    header = LIQ_HEADER.format(
        callback_secret=callback_secret,
        source_password=source_password,
        crossfade_line=_crossfade_line(bs.crossfade_sec),
        encode_format=encode_format_liquidsoap(bs),
        genre=bs.genre.replace('"', "'")[:80],
    )
    desc = station.description.replace('"', "'")[:200]
    name = station.name.replace('"', "'")
    mount = station.icecast_mount
    call = (
        f'make_station(slug="{station.slug}", mount="{mount}", '
        f'name="{name}", description="{desc}")\n'
    )
    return header + call


def regenerate_liquidsoap_config(db: Session) -> None:
    """
    Write one Liquidsoap script per station plus a manifest.
    The liquidsoap supervisor starts/stops individual processes without
    interrupting unrelated stations.
    """
    stations = db.query(Station).order_by(Station.id.asc()).all()
    known_slugs = {s.slug for s in stations}

    STATIONS_LIQ_DIR.mkdir(parents=True, exist_ok=True)

    for station in stations:
        script_path = STATIONS_LIQ_DIR / f"{station.slug}.liq"
        if station.enabled:
            script_path.write_text(_station_script(station, db), encoding="utf-8")
        elif script_path.exists():
            script_path.unlink()

    for orphan in STATIONS_LIQ_DIR.glob("*.liq"):
        if orphan.stem not in known_slugs:
            orphan.unlink()

    manifest = {
        "version": 1,
        "stations": [
            {
                "slug": s.slug,
                "enabled": s.enabled,
                "script": f"stations/{s.slug}.liq",
            }
            for s in stations
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    legacy = LIQUIDSOAP_DIR / "radio.liq"
    if legacy.exists():
        legacy.unlink()
