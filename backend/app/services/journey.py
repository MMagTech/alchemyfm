"""Clock-anchored 'journey' stations: drift a station's vibe A->B over the day.

The plugin bakes an ordered path of waypoint track ids (from AudioMuse's
find_path) into the station's programming at deploy time. Here we pick which
waypoint is "current" based on the local hour, so the vibe walks from the start
(overnight) toward the destination (evening) and back — a smooth daily loop with
its seam in the quiet early-morning hours. The backend then pulls tracks similar
to the current waypoint, so no per-refill path recomputation is needed.
"""

from __future__ import annotations

from typing import Sequence

from app.services.daypart import local_hour


def waypoint_index(hour: int, n: int) -> int:
    """Index into an n-waypoint path for a given hour (0-23).

    Daily arc: trough (start, index 0) at ~06:00, peak (destination, index n-1)
    at ~18:00, descending back overnight. Ping-pongs so it never snaps.
    """
    if n <= 1:
        return 0
    h = hour % 24
    if 6 <= h <= 18:
        frac = (h - 6) / 12.0            # 0 at 06:00 -> 1 at 18:00
    else:
        hh = h if h > 18 else h + 24     # unwrap 18:00 -> 06:00 next day
        frac = 1.0 - (hh - 18) / 12.0
    return min(n - 1, max(0, round(frac * (n - 1))))


def current_seed(waypoints: Sequence[str], tz_name: str) -> str | None:
    """The waypoint track id to seed from right now, or None if no waypoints."""
    if not waypoints:
        return None
    idx = waypoint_index(local_hour(tz_name), len(waypoints))
    return str(waypoints[idx])
