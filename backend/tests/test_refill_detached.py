"""Regression: the programming tier must not read ORM attributes lazily.

fetch_without_holding_db commits and closes the session before awaiting, and
commit expires attributes (expire_on_commit defaults to True). An `async def`
body runs at await time, so passing fetch_programming_batch(station, n) into it
read station.source_type *after* detachment and raised DetachedInstanceError --
silently killing the programming tier on every refill.
"""

import asyncio

from app.services.refill import programming_fetch_args


class _Station:
    """Stands in for a Station that detaches once the session is released."""

    def __init__(self):
        self.released = False
        self._source_type = "clap_query"
        self._source_ref = "warm analog"
        self._programming_json = '{"programming": {"type": "clap_query"}}'

    def _get(self, name):
        if self.released:
            raise RuntimeError(
                "Instance <Station> is not bound to a Session; "
                "attribute refresh operation cannot proceed"
            )
        return getattr(self, name)

    @property
    def source_type(self):
        return self._get("_source_type")

    @property
    def source_ref(self):
        return self._get("_source_ref")

    @property
    def programming_json(self):
        return self._get("_programming_json")


def test_args_are_snapshotted_eagerly():
    station = _Station()
    args = programming_fetch_args(station, 30)
    assert args == (
        "clap_query",
        "warm analog",
        30,
        '{"programming": {"type": "clap_query"}}',
    )


def test_snapshot_survives_session_release():
    """The whole point: values stay usable after the station detaches."""
    station = _Station()
    args = programming_fetch_args(station, 30)  # read while attached
    station.released = True  # session committed + closed

    async def consume(fetch_args):
        return fetch_args  # runs after release, must not touch the station

    assert asyncio.run(consume(args)) == args


def test_lazy_read_after_release_would_raise():
    """Confirms the fake reproduces the original failure mode."""
    station = _Station()
    station.released = True
    try:
        programming_fetch_args(station, 30)
    except RuntimeError as exc:
        assert "not bound to a Session" in str(exc)
    else:
        raise AssertionError("expected a detached-instance style failure")
