"""Harmonic + tempo track sequencing for queue refills.

Pure logic: given a batch of candidate tracks and their AudioMuse analysis
(tempo + musical key/scale), reorder them into a smooth chain so adjacent
tracks share a compatible key (Camelot-wheel mixing) and tempo. Anchored on
the last-played track when its analysis is known.

No I/O here — the caller supplies scores. This keeps the audible logic unit
testable and independent of how/where scores are fetched.
"""

from __future__ import annotations

from typing import Iterable, Sequence, TypeVar

# Camelot wheel: pitch class -> wheel number, split by mode.
# Major keys sit on the "B" ring, minor keys on the "A" ring.
_PITCH_TO_CAMELOT_MAJOR = {
    "C": 8, "C#": 3, "DB": 3, "D": 10, "D#": 5, "EB": 5, "E": 12, "F": 7,
    "F#": 2, "GB": 2, "G": 9, "G#": 4, "AB": 4, "A": 11, "A#": 6, "BB": 6, "B": 1,
}
_PITCH_TO_CAMELOT_MINOR = {
    "C": 5, "C#": 12, "DB": 12, "D": 7, "D#": 2, "EB": 2, "E": 9, "F": 4,
    "F#": 11, "GB": 11, "G": 6, "G#": 1, "AB": 1, "A": 8, "A#": 3, "BB": 3, "B": 10,
}

# How much each dimension counts toward a transition being "smooth".
KEY_WEIGHT = 0.6
BPM_WEIGHT = 0.4


def to_camelot(key: str | None, scale: str | None) -> tuple[int, str] | None:
    """Map an AudioMuse (key, scale) like ("C#", "minor") to a Camelot code.

    Returns (wheel_number, ring) where ring is "A" (minor) or "B" (major),
    or None when the key can't be parsed.
    """
    if not key:
        return None
    pitch = str(key).strip().upper()
    if not pitch:
        return None
    mode = (scale or "major").strip().lower()
    table = _PITCH_TO_CAMELOT_MINOR if mode.startswith("min") else _PITCH_TO_CAMELOT_MAJOR
    number = table.get(pitch)
    if number is None:
        return None
    return number, ("A" if mode.startswith("min") else "B")


def key_compat(a: tuple[int, str] | None, b: tuple[int, str] | None) -> float:
    """Harmonic compatibility of two Camelot codes, in [0, 1].

    Neighbours on the wheel and relative major/minor score highest; distant
    keys score low. Unknown keys are neutral so they don't distort the chain.
    """
    if a is None or b is None:
        return 0.5
    na, la = a
    nb, lb = b
    steps = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        # Same mode: adjacency around the 12-step ring.
        return max(0.0, 1.0 - steps / 6.0)
    # Different mode.
    if steps == 0:
        return 0.9  # relative major/minor — a classic smooth switch
    return max(0.0, 0.6 - steps / 6.0)


def bpm_compat(a: float | None, b: float | None) -> float:
    """Tempo compatibility in [0, 1], tolerant of double/half-time mixing."""
    if not a or not b or a <= 0 or b <= 0:
        return 0.5
    hi, lo = (a, b) if a >= b else (b, a)
    ratio = hi / lo
    # Fold octaves: 120 over 60 is a clean double-time match, not a clash.
    while ratio > 1.5:
        ratio /= 2.0
    return max(0.0, 1.0 - abs(ratio - 1.0) / 0.5)


def transition_score(cur: dict | None, nxt: dict | None) -> float:
    """Weighted key+tempo smoothness of playing `nxt` right after `cur`."""
    cur = cur or {}
    nxt = nxt or {}
    k = key_compat(
        to_camelot(cur.get("key"), cur.get("scale")),
        to_camelot(nxt.get("key"), nxt.get("scale")),
    )
    b = bpm_compat(_as_float(cur.get("tempo")), _as_float(nxt.get("tempo")))
    return KEY_WEIGHT * k + BPM_WEIGHT * b


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_analyzed(score: dict | None) -> bool:
    if not score:
        return False
    has_tempo = _as_float(score.get("tempo")) is not None
    has_key = to_camelot(score.get("key"), score.get("scale")) is not None
    return has_tempo or has_key


T = TypeVar("T")


def harmonic_order(
    refs: Sequence[T],
    scores_by_id: dict[str, dict],
    *,
    seed_score: dict | None = None,
    key_of=lambda r: r.item_id,
) -> list[T]:
    """Reorder `refs` into a smooth transition chain.

    Greedy nearest-compatible walk: starting from the last-played track's
    analysis (`seed_score`) when known, repeatedly append the remaining track
    with the best transition from the current one. Tracks with no usable
    analysis keep their original relative order and trail the chain, so a
    partially-analyzed library degrades gracefully instead of shuffling.
    """
    analyzed: list[T] = []
    unanalyzed: list[T] = []
    for ref in refs:
        score = scores_by_id.get(key_of(ref))
        (analyzed if _is_analyzed(score) else unanalyzed).append(ref)

    if len(analyzed) <= 1:
        return list(analyzed) + list(unanalyzed)

    remaining = list(analyzed)
    ordered: list[T] = []

    current = seed_score if _is_analyzed(seed_score) else None
    if current is None:
        # No usable anchor — start from the first candidate to seed the chain.
        first = remaining.pop(0)
        ordered.append(first)
        current = scores_by_id.get(key_of(first))

    while remaining:
        best_idx = 0
        best_score = -1.0
        for idx, cand in enumerate(remaining):
            s = transition_score(current, scores_by_id.get(key_of(cand)))
            if s > best_score:
                best_score = s
                best_idx = idx
        pick = remaining.pop(best_idx)
        ordered.append(pick)
        current = scores_by_id.get(key_of(pick))

    return ordered + unanalyzed
