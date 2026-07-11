"""AudioMuse client parsing tests."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.audiomuse import AudioMuseClient


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tracks_clap_query():
    respx.post("http://localhost:8000/api/clap/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"item_id": "t1", "title": "One", "author": "Artist"},
                ]
            },
        )
    )
    client = AudioMuseClient()
    client.base_url = "http://localhost:8000"
    tracks = await client.fetch_tracks("clap_query", "smooth jazz", 5)
    assert len(tracks) == 1
    assert tracks[0].item_id == "t1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tracks_similar_seed():
    respx.get("http://localhost:8000/api/similar_tracks").mock(
        return_value=Response(
            200,
            json=[{"item_id": "s1", "title": "Seed Neighbor", "artist": "Band"}],
        )
    )
    client = AudioMuseClient()
    client.base_url = "http://localhost:8000"
    tracks = await client.fetch_tracks(
        "similar_seed",
        "seed-id",
        5,
        programming_json='{"programming":{"seed_id":"seed-id"}}',
    )
    assert len(tracks) == 1
    assert tracks[0].item_id == "s1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tracks_mood_centroid():
    respx.get("http://localhost:8000/api/similar_tracks").mock(
        return_value=Response(
            200,
            json=[{"item_id": "m1", "title": "Mood Track", "author": "Mood Artist"}],
        )
    )
    client = AudioMuseClient()
    client.base_url = "http://localhost:8000"
    tracks = await client.fetch_tracks(
        "mood_centroid",
        "energetic:0",
        5,
        programming_json='{"programming":{"mood":"energetic","centroid_index":0}}',
    )
    assert len(tracks) == 1
