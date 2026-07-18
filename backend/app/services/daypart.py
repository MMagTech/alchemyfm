"""Time-of-day energy shaping for queue refills.

Pure logic: map the current local hour to a target energy *level* (0 = calmest,
1 = most energetic) via a preset curve, then select the tracks in a refill batch
whose energy percentile-rank sits closest to that target.

Percentile ranking (not absolute energy) is deliberate — AudioMuse energy values
are compressed into a narrow band (~0.1–0.2), so absolute thresholds would put
every track in the same bucket. Ranking within the batch stays meaningful
regardless of the library's absolute scale.
"""

from __future__ import annotations

import bisect
from typing import Sequence, TypeVar

DEFAULT_PRESET = "rise_and_settle"

# Hour-of-day (0..23) -> target energy level in [0, 1].
PRESETS: dict[str, tuple[float, ...]] = {
    # Calm overnight and early morning, building to an early-evening peak.
    "rise_and_settle": (
        0.20, 0.18, 0.15, 0.15, 0.18, 0.25, 0.32, 0.40,
        0.48, 0.52, 0.55, 0.58, 0.60, 0.60, 0.62, 0.65,
        0.70, 0.78, 0.82, 0.80, 0.72, 0.60, 0.45, 0.30,
    ),
    # Gentle mornings, steady lively afternoons and evenings.
    "morning_calm": (
        0.35, 0.30, 0.28, 0.28, 0.28, 0.30, 0.30, 0.32,
        0.35, 0.40, 0.48, 0.55, 0.60, 0.62, 0.65, 0.65,
        0.65, 0.68, 0.68, 0.65, 0.60, 0.55, 0.48, 0.40,
    ),
    # Mellow daytime, peak energy after dark.
    "late_night_energy": (
        0.75, 0.78, 0.70, 0.55, 0.40, 0.30, 0.28, 0.30,
        0.35, 0.38, 0.40, 0.42, 0.45, 0.45, 0.45, 0.48,
        0.50, 0.55, 0.60, 0.65, 0.70, 0.78, 0.82, 0.80,
    ),
}


def target_energy(preset: str, hour: int) -> float:
    """Target energy level [0, 1] for a preset at a given hour of day."""
    curve = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    return curve[hour % 24]


def local_hour(tz_name: str) -> int:
    """Current hour (0-23) in the given IANA timezone, falling back to UTC."""
    from datetime import datetime, timezone

    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).hour
        except Exception:
            pass
    return datetime.now(timezone.utc).hour


def _energy(score: dict | None) -> float | None:
    if not score:
        return None
    value = score.get("energy")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


T = TypeVar("T")


def select_by_energy(
    refs: Sequence[T],
    scores_by_id: dict[str, dict],
    target_level: float,
    count: int,
    *,
    key_of=lambda r: r.item_id,
) -> list[T]:
    """Pick `count` tracks whose energy percentile-rank is closest to target_level.

    Tracks with unknown energy are treated as mid-pack so they aren't all
    dropped. When too few tracks carry analysis to rank, falls back to the head
    of the batch. Preserves the batch's original relative order among the picks.
    """
    items = list(refs)
    if count <= 0:
        return []
    if len(items) <= count:
        return items

    energies = [_energy(scores_by_id.get(key_of(r))) for r in items]
    known = sorted(e for e in energies if e is not None)
    if len(known) < 2:
        return items[:count]

    def percentile(e: float | None) -> float:
        if e is None:
            return 0.5
        return bisect.bisect_left(known, e) / (len(known) - 1)

    ranked = sorted(
        range(len(items)),
        key=lambda i: abs(percentile(energies[i]) - target_level),
    )
    chosen = sorted(ranked[:count])  # keep original relative order among picks
    return [items[i] for i in chosen]
