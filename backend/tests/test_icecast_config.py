"""Regression test: write_icecast_config regenerates icecast.xml on every
backend startup and Broadcast Settings save, so hand-editing the checked-in
XML directly does not survive a restart -- the queue-size (and other limits)
must be correct in the generator itself.
"""

from __future__ import annotations

from app.services.icecast_config import write_icecast_config


def test_write_icecast_config_queue_size_gives_mobile_listeners_slack():
    path = write_icecast_config(max_listeners=20)
    content = path.read_text(encoding="utf-8")
    assert "<queue-size>1048576</queue-size>" in content
    assert "<clients>20</clients>" in content
