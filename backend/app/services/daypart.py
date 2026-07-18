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


# Mood arcs, keyed to the tags AudioMuse ships in `other_features`
# (danceable, aggressive, happy, party, relaxed, sad). Hour-of-day -> tag.
MOOD_PRESETS: dict[str, tuple[str, ...]] = {
    # Quiet nights, bright days, party evenings.
    "calm_to_party": (
        "relaxed", "relaxed", "relaxed", "relaxed", "relaxed", "relaxed",
        "happy", "happy", "happy", "happy", "happy", "happy",
        "happy", "happy", "danceable", "danceable", "danceable", "danceable",
        "party", "party", "party", "party", "relaxed", "relaxed",
    ),
    # Easy all day — background listening.
    "steady_relaxed": tuple(["relaxed"] * 24),
    # Upbeat daytime, wind down after dark.
    "upbeat_days": (
        "relaxed", "relaxed", "relaxed", "relaxed", "relaxed", "relaxed",
        "happy", "happy", "danceable", "danceable", "danceable", "danceable",
        "danceable", "danceable", "danceable", "danceable", "happy", "happy",
        "happy", "happy", "relaxed", "relaxed", "relaxed", "relaxed",
    ),
}

# How much the mood tag counts vs energy when both are active.
MOOD_WEIGHT = 0.4
ENERGY_WEIGHT = 0.6


def target_mood(preset: str, hour: int) -> str | None:
    """Mood tag to favour at this hour, or None when the preset is unknown/off."""
    curve = MOOD_PRESETS.get(preset)
    if not curve:
        return None
    return curve[hour % 24]


def parse_tag_scores(value) -> dict[str, float]:
    """Parse AudioMuse's "tag:score,tag:score" strings into a dict."""
    if not value:
        return {}
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            try:
                out[str(k).strip().lower()] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    scores: dict[str, float] = {}
    for part in str(value).split(","):
        tag, _, raw = part.partition(":")
        tag = tag.strip().lower()
        if not tag:
            continue
        try:
            scores[tag] = float(raw)
        except (TypeError, ValueError):
            continue
    return scores


def _mood_score(score: dict | None, tag: str) -> float | None:
    if not score or not tag:
        return None
    tags = parse_tag_scores(score.get("other_features"))
    if tag in tags:
        return tags[tag]
    tags = parse_tag_scores(score.get("mood_vector"))
    return tags.get(tag)


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


def select_by_daypart(
    refs: Sequence[T],
    scores_by_id: dict[str, dict],
    count: int,
    *,
    energy_level: float | None = None,
    mood_tag: str | None = None,
    key_of=lambda r: r.item_id,
) -> list[T]:
    """Pick `count` tracks matching the hour's energy target and/or mood tag.

    Energy is scored by percentile distance from the target (absolute values are
    useless — AudioMuse compresses energy into a narrow band). Mood is scored by
    the track's own value for the tag, also percentile-ranked so the two are
    comparable. With only one dimension active this reduces to that dimension;
    with neither, the batch is returned untouched.
    """
    items = list(refs)
    if count <= 0:
        return []
    if len(items) <= count:
        return items
    if energy_level is None and not mood_tag:
        return items[:count]
    if mood_tag is None:
        return select_by_energy(refs, scores_by_id, energy_level or 0.5, count, key_of=key_of)

    energies = [_energy(scores_by_id.get(key_of(r))) for r in items]
    moods = [_mood_score(scores_by_id.get(key_of(r)), mood_tag) for r in items]

    known_e = sorted(e for e in energies if e is not None)
    known_m = sorted(m for m in moods if m is not None)
    if len(known_m) < 2 and len(known_e) < 2:
        return items[:count]

    def pct(value, pool):
        if value is None or len(pool) < 2:
            return 0.5
        return bisect.bisect_left(pool, value) / (len(pool) - 1)

    def cost(i: int) -> float:
        # Energy: distance from target. Mood: distance from "as much as possible".
        parts: list[tuple[float, float]] = []
        if energy_level is not None and len(known_e) >= 2:
            parts.append((ENERGY_WEIGHT, abs(pct(energies[i], known_e) - energy_level)))
        if len(known_m) >= 2:
            parts.append((MOOD_WEIGHT, 1.0 - pct(moods[i], known_m)))
        if not parts:
            return 0.5
        total = sum(w for w, _ in parts)
        return sum(w * d for w, d in parts) / total

    ranked = sorted(range(len(items)), key=cost)
    chosen = sorted(ranked[:count])
    return [items[i] for i in chosen]
