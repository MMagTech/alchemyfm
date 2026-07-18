"""Unit tests for time-of-day energy shaping (pure logic, no I/O)."""

from app.schemas import TrackRef
from app.services.daypart import DEFAULT_PRESET, PRESETS, select_by_energy, target_energy


def test_target_energy_curve_and_hour_wrap():
    assert target_energy("rise_and_settle", 3) == PRESETS["rise_and_settle"][3]
    assert target_energy("rise_and_settle", 27) == PRESETS["rise_and_settle"][3]  # 27 % 24


def test_target_energy_unknown_preset_falls_back():
    assert target_energy("does_not_exist", 12) == PRESETS[DEFAULT_PRESET][12]


def _ref(item_id: str) -> TrackRef:
    return TrackRef(item_id=item_id, title=item_id, artist="x")


_SCORES = {"lo": {"energy": 0.10}, "mid": {"energy": 0.15}, "hi": {"energy": 0.20}}


def test_selects_high_energy_when_target_high():
    refs = [_ref("lo"), _ref("mid"), _ref("hi")]
    assert [r.item_id for r in select_by_energy(refs, _SCORES, 1.0, 1)] == ["hi"]


def test_selects_low_energy_when_target_low():
    refs = [_ref("lo"), _ref("mid"), _ref("hi")]
    assert [r.item_id for r in select_by_energy(refs, _SCORES, 0.0, 1)] == ["lo"]


def test_trims_to_count_and_preserves_order():
    refs = [_ref(str(i)) for i in range(5)]
    scores = {str(i): {"energy": 0.10 + i * 0.02} for i in range(5)}
    # Two highest-energy are "3","4"; kept in original relative order.
    assert [r.item_id for r in select_by_energy(refs, scores, 1.0, 2)] == ["3", "4"]


def test_falls_back_to_head_when_unranked():
    refs = [_ref("a"), _ref("b"), _ref("c")]
    assert [r.item_id for r in select_by_energy(refs, {}, 0.5, 2)] == ["a", "b"]


def test_returns_all_when_fewer_than_count():
    refs = [_ref("a"), _ref("b")]
    assert len(select_by_energy(refs, _SCORES, 0.5, 5)) == 2
