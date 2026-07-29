"""filter_track_refs must loosen artist rules rather than return a short batch."""

from app.schemas import TrackRef
from app.services.refill import filter_track_refs


def _ref(item_id: str, artist: str) -> TrackRef:
    return TrackRef(item_id=item_id, title=item_id, artist=artist)


def test_healthy_case_keeps_one_per_artist():
    """Plenty of variety available -> unchanged strict behaviour."""
    refs = [_ref(f"t{i}", f"artist{i}") for i in range(10)]
    picked = filter_track_refs(refs, set(), set(), 5)
    assert len(picked) == 5
    assert len({r.artist for r in picked}) == 5  # all distinct artists


def test_relaxes_artist_cap_instead_of_starving():
    """One artist dominates: strict pass yields 1, so the cap must loosen."""
    refs = [_ref(f"t{i}", "solo") for i in range(10)]
    picked = filter_track_refs(refs, set(), set(), 5)
    assert len(picked) == 5, "should fill the batch by relaxing the per-artist cap"
    assert len({r.item_id for r in picked}) == 5, "must not repeat a track"


def test_relaxes_artist_separation_as_last_resort():
    """Every available artist is inside the separation window."""
    refs = [_ref(f"t{i}", "blocked") for i in range(6)]
    picked = filter_track_refs(refs, set(), {"blocked"}, 4)
    assert len(picked) == 4
    assert len({r.item_id for r in picked}) == 4


def test_never_relaxes_exclude():
    """Queued/just-played tracks stay excluded even when starving."""
    refs = [_ref("a", "x"), _ref("b", "x"), _ref("c", "x")]
    picked = filter_track_refs(refs, {"a", "b"}, set(), 5)
    assert [r.item_id for r in picked] == ["c"], "excluded ids must never come back"


def test_no_duplicates_across_relaxation_passes():
    refs = [_ref(f"t{i}", "same") for i in range(4)]
    picked = filter_track_refs(refs, set(), {"same"}, 10)
    ids = [r.item_id for r in picked]
    assert len(ids) == len(set(ids)) == 4


def test_respects_target_ceiling():
    refs = [_ref(f"t{i}", f"a{i}") for i in range(20)]
    assert len(filter_track_refs(refs, set(), set(), 3)) == 3
