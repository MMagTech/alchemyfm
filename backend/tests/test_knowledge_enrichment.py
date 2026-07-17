"""Unit tests for knowledge enrichment quality helpers (no network).

Covers the fact-quality improvements: Wikipedia section extraction, MusicBrainz
recording selection / credit relationships, and wiring the artist biography into
the snippet pipeline.
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import respx

from app.config import settings as app_settings
from app.knowledge import wikipedia, musicbrainz, ollama, enrich, cloud, llm
from app.knowledge import settings as ksettings
from app.knowledge.cache import validate_facts
from app.knowledge.snippets import merge_snippets


# --- Wikipedia section extraction -----------------------------------------

SAMPLE_EXTRACT = """"Test Song" is a song by Some Artist, released in 2011.


== Composition ==
The song is in the key of C. It runs at 120 BPM.


== Recording and production ==
It was recorded in one take.

=== Mixing ===
Mixed by a famous engineer.


== Charts ==
It reached number one.
"""


def test_split_sections_returns_lead_and_sections():
    lead, sections = wikipedia._split_sections(SAMPLE_EXTRACT)
    assert lead.startswith('"Test Song" is a song')
    headers = [h for h, _ in sections]
    assert "Composition" in headers
    assert "Recording and production" in headers


def test_split_sections_flattens_subheaders_no_marker_leak():
    _, sections = wikipedia._split_sections(SAMPLE_EXTRACT)
    body = dict(sections)["Recording and production"]
    assert "==" not in body  # no raw === Mixing === markers
    assert "Mixing:" in body  # subheader flattened to inline label
    assert "recorded in one take" in body


def test_article_snippets_prefers_trivia_sections_with_anchor_urls():
    article = {"title": "Test Song", "extract": SAMPLE_EXTRACT, "is_disambig": False}
    snippets = wikipedia._article_snippets(article, section_budget=5)
    # lead + Composition + Recording (Charts is not in the trivia allowlist)
    urls = [s["url"] for s in snippets]
    assert urls[0] == "https://en.wikipedia.org/wiki/Test_Song"
    assert any("#Composition" in u for u in urls)
    assert any("#Recording_and_production" in u for u in urls)
    assert not any("#Charts" in u for u in urls)


# --- MusicBrainz recording selection --------------------------------------

def _rec(title, *, disambig="", artist="Some Artist", releases=None):
    return {
        "title": title,
        "disambiguation": disambig,
        "artist-credit": [{"artist": {"name": artist}}],
        "releases": [{"title": t} for t in (releases or [])],
    }


def test_pick_recording_prefers_album_over_documentary():
    track = {"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera"}
    documentary = _rec("Bohemian Rhapsody", artist="Queen",
                       releases=["The Making of A Night at the Opera"])
    studio = _rec("Bohemian Rhapsody", artist="Queen",
                  releases=["A Night at the Opera"])
    assert musicbrainz._pick_recording([documentary, studio], track) is studio


def test_pick_recording_penalises_remix_and_prefers_exact_title():
    track = {"title": "Midnight City", "artist": "M83", "album": "Hurry Up, We're Dreaming"}
    interlude = _rec("On Midnight City", artist="M83")
    remix = _rec("Midnight City", disambig="remix", artist="M83")
    exact = _rec("Midnight City", artist="M83",
                 releases=["Hurry Up, We're Dreaming"])
    assert musicbrainz._pick_recording([interlude, remix, exact], track) is exact


def test_credit_snippets_extracts_producer_and_sample():
    detail = {
        "relations": [
            {"type": "producer", "artist": {"name": "Mike Will Made It"}},
            {
                "type": "samples",
                "recording": {
                    "title": "Some Old Song",
                    "artist-credit": [{"artist": {"name": "Old Artist"}}],
                },
            },
        ]
    }
    snippets = musicbrainz._credit_snippets(detail, "abc123", "New Song")
    assert len(snippets) == 1
    text = snippets[0]["snippet"]
    assert "produced by Mike Will Made It" in text
    assert 'Samples "Some Old Song" by Old Artist' in text
    assert snippets[0]["url"].endswith("abc123")


def test_credit_snippets_empty_when_no_relations():
    assert musicbrainz._credit_snippets({"relations": []}, "id", "Title") == []


# --- Artist biography wiring ----------------------------------------------

def test_bio_snippets_uses_lastfm_url():
    track = {"artist": "Some Artist", "artist_mbid": "mbid-1"}
    info = SimpleNamespace(biography="A rich biography.", info_url="https://last.fm/x")
    snippets = enrich._bio_snippets(track, info)
    assert snippets == [
        {
            "url": "https://last.fm/x",
            "title": "Some Artist — artist biography",
            "snippet": "A rich biography.",
        }
    ]


def test_bio_snippets_falls_back_to_musicbrainz_url():
    track = {"artist": "Some Artist", "artist_mbid": "mbid-1"}
    info = SimpleNamespace(biography="Bio text.", info_url=None)
    snippets = enrich._bio_snippets(track, info)
    assert snippets[0]["url"] == "https://musicbrainz.org/artist/mbid-1"


def test_bio_snippets_empty_without_bio_or_url():
    track = {"artist": "Some Artist", "artist_mbid": ""}
    assert enrich._bio_snippets(track, SimpleNamespace(biography="", info_url="")) == []
    assert enrich._bio_snippets(track, SimpleNamespace(biography="Bio", info_url="")) == []


# --- Ollama JSON parsing ---------------------------------------------------

def test_extract_json_handles_plain_and_wrapped():
    assert ollama._extract_json('{"facts": []}') == {"facts": []}
    assert ollama._extract_json('Here you go:\n{"facts": [1]}\ndone')["facts"] == [1]


# --- Cloud provider + dispatch ---------------------------------------------

def test_effective_llm_provider_defaults_and_validates(monkeypatch):
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "openai")
    assert ksettings.effective_llm_provider() == "openai"
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "BOGUS")
    assert ksettings.effective_llm_provider() == "ollama"
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "")
    assert ksettings.effective_llm_provider() == "ollama"


def test_model_label_is_provider_aware(monkeypatch):
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "openai")
    monkeypatch.setattr(app_settings, "knowledge_llm_model", "gpt-5-nano")
    assert ksettings.effective_model_label() == "gpt-5-nano"
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "ollama")
    monkeypatch.setattr(app_settings, "ollama_model", "qwen2.5:3b")
    assert ksettings.effective_model_label() == "qwen2.5:3b"


def test_build_user_prompt_includes_track_and_snippets():
    prompt = ollama.build_user_prompt(
        {"title": "Redbone", "artist": "Childish Gambino", "album": "AML", "year": 2016},
        [{"title": "t", "url": "https://u", "snippet": "a funky bassline"}],
    )
    assert "Redbone" in prompt and "Childish Gambino" in prompt
    assert "AML" in prompt and "2016" in prompt
    assert "a funky bassline" in prompt


def test_llm_dispatches_to_cloud_when_provider_openai(monkeypatch):
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "openai")
    monkeypatch.setattr(app_settings, "knowledge_llm_base_url", "https://x/v1")
    monkeypatch.setattr(app_settings, "knowledge_llm_model", "gpt-5-nano")
    monkeypatch.setattr(app_settings, "knowledge_llm_api_key", "sk-test")
    calls = {}

    async def fake_cloud(base, key, model, track, snippets, max_facts, max_chars):
        calls["cloud"] = (base, key, model, max_facts, max_chars)
        return [{"category": "song_fact"}]

    async def fake_ollama(*a, **k):
        calls["ollama"] = True
        return []

    monkeypatch.setattr(cloud, "summarize_facts", fake_cloud)
    monkeypatch.setattr(ollama, "summarize_facts", fake_ollama)

    out = asyncio.run(llm.summarize(None, {"title": "T"}, [{"snippet": "s"}], 3, 300))
    assert out == [{"category": "song_fact"}]
    assert "ollama" not in calls
    assert calls["cloud"] == ("https://x/v1", "sk-test", "gpt-5-nano", 3, 300)


def test_llm_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr(app_settings, "knowledge_llm_provider", "ollama")
    calls = {}

    async def fake_cloud(*a, **k):
        calls["cloud"] = True
        return []

    async def fake_ollama(base, model, track, snippets, max_facts, max_chars):
        calls["ollama"] = (base, model, max_chars)
        return []

    monkeypatch.setattr(cloud, "summarize_facts", fake_cloud)
    monkeypatch.setattr(ollama, "summarize_facts", fake_ollama)

    asyncio.run(llm.summarize(None, {"title": "T"}, [{"snippet": "s"}], 3, 300))
    assert "cloud" not in calls
    assert calls["ollama"][2] == 300


@respx.mock
def test_cloud_summarize_parses_openai_response():
    route = respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"facts":[{"category":"song_fact",'
                                            '"text":"x","confidence":0.9,'
                                            '"sources":[{"url":"https://x","title":"X"}]}]}'}}
                ]
            },
        )
    )
    facts = asyncio.run(
        cloud.summarize_facts(
            "https://api.example.com/v1", "sk-abc", "gpt-5-nano",
            {"title": "T", "artist": "A"},
            [{"title": "t", "url": "https://u", "snippet": "s"}],
            3,
        )
    )
    assert facts[0]["category"] == "song_fact"
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-abc"
    sent = json.loads(req.content)
    assert sent["model"] == "gpt-5-nano"
    assert sent["response_format"] == {"type": "json_object"}
    assert "T" in sent["messages"][1]["content"]  # shared user prompt carries the track
    # Reasoning-tier models 400 on any non-default temperature -- never send one.
    assert "temperature" not in sent


@respx.mock
def test_cloud_summarize_surfaces_provider_error_body():
    """A 400's body says what the provider rejected; raise_for_status() drops it."""
    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Unsupported value: 'temperature' does not support 0"}},
        )
    )
    try:
        asyncio.run(
            cloud.summarize_facts(
                "https://api.example.com/v1", "sk-abc", "gpt-5-mini",
                {"title": "T"}, [{"title": "t", "url": "https://u", "snippet": "s"}], 3,
            )
        )
    except RuntimeError as exc:
        assert "400" in str(exc)
        assert "temperature" in str(exc)  # the provider's reason reaches the admin panel
    else:
        raise AssertionError("expected RuntimeError carrying the response body")


def test_cloud_summarize_requires_key():
    try:
        asyncio.run(cloud.summarize_facts("https://x/v1", "", "m", {}, [{"snippet": "s"}], 3))
    except RuntimeError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing API key")


def test_prompt_carries_max_chars_and_bans_metadata_facts():
    """The model must know its length budget and what counts as non-trivia."""
    rendered = ollama.SYSTEM_PROMPT.format(max_facts=3, max_chars=300)
    assert "under 300 characters" in rendered
    assert "{" not in rendered.replace('{{', '').replace('}}', '') or '"facts"' in rendered
    for banned in ("running time", "track number", "active years", "lineup"):
        assert banned in rendered.lower()


@respx.mock
def test_cloud_summarize_passes_max_chars_into_system_prompt():
    route = respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"facts":[]}'}}]})
    )
    asyncio.run(
        cloud.summarize_facts(
            "https://api.example.com/v1", "sk", "m",
            {"title": "T"}, [{"title": "t", "url": "https://u", "snippet": "s"}],
            3, 300,
        )
    )
    system = json.loads(route.calls.last.request.content)["messages"][0]["content"]
    assert "under 300 characters" in system


def test_clip_marks_the_cut_and_never_leaves_a_half_name():
    """A bare text[:limit] made the model report "Chink Sa" as a producer."""
    from app.knowledge.snippets import clip

    short = "Produced by Buckwild and Bink!"
    assert clip(short, 600) == short  # under the limit: untouched, no ellipsis

    text = "features production from Buckwild, Irv Gotti, Alchemist, Bink! and Chink Santana"
    out = clip(text, 74)  # lands mid-"Santana"
    assert out.endswith("…")  # the model can now see the tail is unreliable
    assert not out.rstrip("…").endswith("Sa")  # no half-word fragment to misread
    assert "Santana" not in out  # the full name is never implied
    assert out.startswith("features production from Buckwild")  # the good part survives
    # A multi-word name can still be half-visible ("...and Chink…"). The word
    # boundary only stops gibberish; the ellipsis plus the prompt rule ("never
    # state a detail sitting at the cut") is what stops the model using it.


def test_merge_snippets_clips_long_source_text():
    target, seen = [], set()
    merge_snippets(
        target, seen,
        [{"url": "https://x", "title": "T", "snippet": "word " * 400}],  # 2000 chars
    )
    assert len(target[0]["snippet"]) <= 601  # 600 + the ellipsis
    assert target[0]["snippet"].endswith("…")


def test_purge_clears_every_job_including_failed_and_done():
    """A stale `failed` row otherwise keeps 'last job error' alive after a purge."""
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.knowledge.cache import purge_all_cache
    from app.knowledge.database import (
        KnowledgeBase,
        KnowledgeJob,
        KnowledgeJobStatus,
        TrackKnowledge,
        TrackKnowledgeStatus,
    )

    engine = create_engine("sqlite://")
    KnowledgeBase.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    db.add(
        TrackKnowledge(
            item_id="a",
            status=TrackKnowledgeStatus.ready,
            payload_json="{}",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=1),
        )
    )
    for st in KnowledgeJobStatus:  # pending, running, done, failed, cancelled
        db.add(KnowledgeJob(item_id="a", station_id=0, status=st))
    db.commit()
    assert db.query(KnowledgeJob).count() == len(list(KnowledgeJobStatus))

    deleted = purge_all_cache(db)

    assert deleted == 1
    assert db.query(TrackKnowledge).count() == 0
    assert db.query(KnowledgeJob).count() == 0
    db.close()


def test_validate_facts_drops_low_confidence_and_sourceless():
    facts = [
        {"category": "song_fact", "text": "Good", "confidence": 0.9,
         "sources": [{"url": "https://x", "title": "X"}]},
        {"category": "song_fact", "text": "Weak", "confidence": 0.4,
         "sources": [{"url": "https://x", "title": "X"}]},
        {"category": "song_fact", "text": "No source", "confidence": 0.9, "sources": []},
        {"category": "bad_cat", "text": "Bad", "confidence": 0.9,
         "sources": [{"url": "https://x", "title": "X"}]},
    ]
    clean = validate_facts(facts, min_confidence=0.6, max_chars=200)
    assert [f["text"] for f in clean] == ["Good"]
