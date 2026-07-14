"""Icecast wire-text metadata can mangle punctuation, which breaks matching
against queue rows -- and a failed match means no item_id, and therefore no
cover art request at all (see app.services.queue._normalize_metadata)."""

from __future__ import annotations

from app.services.queue import _artists_match, _normalize_metadata, _titles_match


def test_normalize_metadata_maps_mangled_asterisk_to_apostrophe():
    # Icecast sometimes renders a curly apostrophe as a literal "*".
    assert _normalize_metadata("Play*n It Raw") == _normalize_metadata("Play'n It Raw")


def test_titles_match_survives_mangled_apostrophe():
    assert _titles_match("Play*n It Raw", "Play'n It Raw")


def test_titles_match_survives_curly_quotes():
    assert _titles_match("Rock ‘n’ Roll", "Rock 'n' Roll")


def test_titles_match_survives_periods_and_punctuation():
    assert _titles_match("B.G. - Intro", "B G Intro")


def test_titles_match_rejects_genuinely_different_titles():
    assert not _titles_match("Play'n It Raw", "Something Else Entirely")


def test_artists_match_survives_mangled_asterisk():
    assert _artists_match("Guns N* Roses", "Guns N' Roses")
