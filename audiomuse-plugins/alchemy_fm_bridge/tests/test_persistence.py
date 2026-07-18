"""Database persistence tests for channel designer."""

from __future__ import annotations

import pytest

from bridge_loader import bridge, requires_postgres


@requires_postgres
class TestPersistence:
    def test_migrate_creates_tables(self, pg_db):
        cur = pg_db.cursor()
        cur.execute(
            "SELECT to_regclass(%s)",
            (bridge.table("channels"),),
        )
        assert cur.fetchone()[0] is not None
        cur.close()

    def test_save_and_load_round_trip(self, pg_db):
        profile = bridge.profile_from_form(
            {
                "name": "Round Trip FM",
                "programming_type": "clap_query",
                "clap_query": "ambient electronic",
                "refresh_mode": "similar_to_last",
            }
        )
        bridge._save_channel(
            profile,
            preview_ids=["rt-1", "rt-2"],
            unfiltered_preview_ids=["rt-1", "rt-2", "rt-3"],
        )
        loaded = bridge._load_saved_channel(profile["station"]["slug"])
        assert loaded is not None
        saved_profile, preview_ids = loaded
        assert preview_ids == ["rt-1", "rt-2"]
        assert saved_profile["programming"]["query"] == "ambient electronic"
        assert saved_profile["last_unfiltered_preview_ids"] == ["rt-1", "rt-2", "rt-3"]

    def test_record_channel_error(self, pg_db):
        bridge._record_channel_error("err-slug", "Preview blew up")
        assert bridge._channel_last_error("err-slug") == "Preview blew up"

    def test_living_pool_add(self, pg_db):
        slug = "living-test"
        bridge._add_to_pool(slug, ["pool-1", "pool-2"], source="preview")
        assert bridge._pool_count(slug) == 2

    def test_audition_history(self, pg_db):
        slug = "audition-test"
        bridge._record_audition(slug, ["a-1", "a-2", "a-3"])
        rows = bridge._audition_history_html(slug)
        assert "3" in rows

    def test_backup_export_includes_channels_and_pools(self, pg_db):
        profile = bridge.profile_from_form(
            {
                "name": "Backup FM",
                "programming_type": "clap_query",
                "clap_query": "warm analog",
                "refresh_mode": "similar_to_last",
            }
        )
        bridge._save_channel(profile, preview_ids=["b-1", "b-2"])
        slug = profile["station"]["slug"]
        bridge._add_to_pool(slug, ["pool-a", "pool-b"], source="preview")

        data = bridge._export_backup_data()
        assert data["format"] == bridge.BACKUP_FORMAT
        ch = next(c for c in data["channels"] if c["slug"] == slug)
        assert "warm analog" in ch["profile_json"]
        assert len(data["pools"][slug]) == 2

    def test_backup_restore_round_trip(self, pg_db):
        profile = bridge.profile_from_form(
            {
                "name": "Restore FM",
                "programming_type": "clap_query",
                "clap_query": "deep space",
                "refresh_mode": "similar_to_last",
            }
        )
        bridge._save_channel(profile, preview_ids=["r-1"])
        slug = profile["station"]["slug"]
        bridge._add_to_pool(slug, ["p-1", "p-2", "p-3"], source="preview")
        backup = bridge._export_backup_data()

        # Simulate data loss, then restore from the backup.
        cur = pg_db.cursor()
        cur.execute(
            "DELETE FROM " + bridge.table("channel_pool") + " WHERE channel_slug = %s",
            (slug,),
        )
        cur.execute(
            "DELETE FROM " + bridge.table("channels") + " WHERE slug = %s",
            (slug,),
        )
        pg_db.commit()
        cur.close()
        assert bridge._load_saved_channel(slug) is None

        summary = bridge._restore_backup_data(backup)
        assert summary["channels"] >= 1
        loaded = bridge._load_saved_channel(slug)
        assert loaded is not None
        restored_profile, _ = loaded
        assert restored_profile["programming"]["query"] == "deep space"
        assert bridge._pool_count(slug) == 3

    def test_backup_restore_rejects_foreign_file(self, pg_db):
        with pytest.raises(bridge.ChannelDesignerError):
            bridge._restore_backup_data({"format": "not_ours", "channels": []})
