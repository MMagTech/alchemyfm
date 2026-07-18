"""Unit tests for harmonic/tempo track ordering (pure logic, no I/O)."""

from app.schemas import TrackRef
from app.services.ordering import (
    bpm_compat,
    harmonic_order,
    key_compat,
    to_camelot,
    transition_score,
)


def test_to_camelot_known_keys():
    assert to_camelot("C", "major") == (8, "B")
    assert to_camelot("A", "minor") == (8, "A")
    assert to_camelot("C#", "minor") == (12, "A")
    assert to_camelot("Db", "major") == (3, "B")  # enharmonic with C#


def test_to_camelot_unparseable():
    assert to_camelot(None, "minor") is None
    assert to_camelot("", "major") is None
    assert to_camelot("H", "major") is None  # not a real pitch class


def test_key_compat_ranking():
    c_maj = to_camelot("C", "major")  # 8B
    a_min = to_camelot("A", "minor")  # 8A — relative minor
    g_maj = to_camelot("G", "major")  # 9B — wheel neighbour
    fs_maj = to_camelot("F#", "major")  # 2B — far side of the wheel

    assert key_compat(c_maj, c_maj) == 1.0
    assert key_compat(c_maj, a_min) == 0.9  # relative major/minor
    assert key_compat(c_maj, g_maj) > key_compat(c_maj, fs_maj)  # neighbour beats far


def test_key_compat_unknown_is_neutral():
    assert key_compat(None, to_camelot("C", "major")) == 0.5


def test_bpm_compat():
    assert bpm_compat(120, 120) == 1.0
    assert bpm_compat(120, 60) == 1.0  # double-time folds to a match
    assert bpm_compat(120, 122) > bpm_compat(120, 138)
    assert bpm_compat(None, 120) == 0.5


def test_transition_score_prefers_compatible():
    cur = {"tempo": 120, "key": "C", "scale": "major"}
    close = {"tempo": 122, "key": "A", "scale": "minor"}  # relative + near tempo
    far = {"tempo": 90, "key": "F#", "scale": "major"}  # distant key + tempo
    assert transition_score(cur, close) > transition_score(cur, far)


def _ref(item_id: str) -> TrackRef:
    return TrackRef(item_id=item_id, title=item_id, artist="x")


def test_harmonic_order_chains_by_compatibility():
    refs = [_ref("a"), _ref("b"), _ref("c")]
    scores = {
        "a": {"tempo": 128, "key": "F#", "scale": "major"},  # 2B, far from seed
        "b": {"tempo": 120, "key": "A", "scale": "minor"},   # 8A, relative to seed
        "c": {"tempo": 121, "key": "G", "scale": "major"},   # 9B, neighbour of seed
    }
    seed = {"tempo": 120, "key": "C", "scale": "major"}  # 8B

    ordered = [r.item_id for r in harmonic_order(refs, scores, seed_score=seed)]

    # The relative-minor/near-tempo track should lead; the far one should trail.
    assert ordered[0] == "b"
    assert ordered[-1] == "a"


def test_harmonic_order_trails_unanalyzed():
    refs = [_ref("known1"), _ref("unknown"), _ref("known2")]
    scores = {
        "known1": {"tempo": 120, "key": "C", "scale": "major"},
        "known2": {"tempo": 121, "key": "G", "scale": "major"},
        # "unknown" has no score at all
    }
    ordered = [r.item_id for r in harmonic_order(refs, scores)]

    assert ordered[-1] == "unknown"
    assert set(ordered) == {"known1", "known2", "unknown"}


def test_harmonic_order_preserves_all_tracks():
    refs = [_ref(str(i)) for i in range(6)]
    scores = {"0": {"tempo": 120, "key": "C", "scale": "major"}}  # only one analyzed
    ordered = harmonic_order(refs, scores)
    assert {r.item_id for r in ordered} == {str(i) for i in range(6)}
    assert len(ordered) == 6
