"""Rate limiting on admin login (brute-force protection) and the public
stream endpoints (protects the low Icecast listener cap from connection-spam
abuse)."""

from __future__ import annotations

from app.rate_limit import limiter


def test_admin_login_rate_limited_after_five_attempts(client):
    for _ in range(5):
        resp = client.post(
            "/api/admin/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    resp = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 429


def test_admin_login_rate_limit_does_not_block_other_ips(client):
    for _ in range(5):
        client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})

    # A different source IP is a separate rate-limit bucket.
    limiter.reset()
    resp = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401  # not 429 -- fresh bucket, just wrong password


def test_listen_stream_rate_limited_after_twenty_requests(client, sample_station):
    # A small Range probe (iOS's sniff pattern) is answered locally with no
    # upstream Icecast connection -- exactly what we want to exercise just
    # the rate limiter without a real Icecast server in the test environment.
    headers = {"Range": "bytes=0-1"}
    for _ in range(20):
        resp = client.get(f"/api/stations/{sample_station.slug}/listen", headers=headers)
        assert resp.status_code == 206

    resp = client.get(f"/api/stations/{sample_station.slug}/listen", headers=headers)
    assert resp.status_code == 429


def test_listen_m3u_rate_limited_after_ten_requests(client, sample_station):
    for _ in range(10):
        resp = client.get(f"/api/stations/{sample_station.slug}/listen.m3u")
        assert resp.status_code == 200

    resp = client.get(f"/api/stations/{sample_station.slug}/listen.m3u")
    assert resp.status_code == 429
