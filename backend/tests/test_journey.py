"""Unit tests for clock-anchored journey waypoint selection (pure logic)."""

from app.services.journey import current_seed, waypoint_index


def test_waypoint_index_daily_arc():
    n = 5
    assert waypoint_index(6, n) == 0          # 06:00 -> start
    assert waypoint_index(18, n) == n - 1     # 18:00 -> destination
    assert waypoint_index(12, n) == 2         # midday -> middle
    # Overnight descends back toward the start.
    assert waypoint_index(0, n) == 2          # midnight -> halfway back
    assert waypoint_index(3, n) < waypoint_index(0, n)


def test_waypoint_index_edges():
    assert waypoint_index(10, 1) == 0         # single waypoint
    assert waypoint_index(10, 0) == 0         # empty guard
    for h in range(24):
        assert 0 <= waypoint_index(h, 4) <= 3  # always in range


def test_current_seed_picks_waypoint():
    waypoints = ["w0", "w1", "w2", "w3", "w4"]
    # UTC 06:00-ish behaviour is time-dependent, so assert membership + endpoints
    assert current_seed(waypoints, "UTC") in waypoints
    assert current_seed([], "UTC") is None
    # Deterministic endpoints via the index function it delegates to.
    assert waypoints[waypoint_index(6, 5)] == "w0"
    assert waypoints[waypoint_index(18, 5)] == "w4"
