# Alchemy FM — Native iOS App Handoff

Context document for starting the **native iOS listener app**. Written for a fresh
Claude Code session on a Mac with Xcode and the iOS Simulator installed. Read this
first, then skim `README.md` for the overall system.

## Why a native app

Alchemy FM's listener UI is a web app / PWA (`web/`). It works well on desktop but
has a persistent, diagnosed problem on iOS:

> iOS suspends the PWA's JavaScript runtime the moment audio stops (e.g. a brief
> underrun at a track transition). The web player's stall-recovery logic runs via
> `setTimeout`, so the recovery timer never fires — the station goes silent until
> the user foregrounds the app. Desktop hits the same transition hiccups and
> silently recovers in ~2 s. (See `web/static/app.js` reconnect scheduling.)

A native app sidesteps this class of problem entirely: `AVPlayer` + a background
audio session keeps playing (and recovering) with the app backgrounded or the
screen locked, and we get real lock-screen / Control Center integration.

## What Alchemy FM is (30 seconds)

Live internet radio, not a music player. The backend (FastAPI, `backend/`) manages
stations and queues; Liquidsoap plays continuously; **Icecast broadcasts one shared
stream per station**. Everyone hears the same broadcast — there is **no skip, no
seek, no pause-and-resume-where-you-left-off, no per-user queue**. The iOS app is
a *radio tuner*: pick a station, listen live, see what's playing.

- Streams are **MP3 or AAC** (operator-configurable in Broadcast settings). Both
  play natively in `AVPlayer` with no extra decoding work.
- Station artwork and track cover art are proxied through the backend — the app
  never talks to Navidrome/AudioMuse directly.
- Admin/operator features are **out of scope** for v1. Listener experience only.
  (Possible v2: operator login + heart button, matching the PWA.)

## Backend API the app will use

All listener endpoints are public (no auth), defined in
`backend/app/routers/stations.py`, response models in `backend/app/schemas.py`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/stations` | List of `StationSummary` — the station grid |
| `GET /api/stations/{slug}` | `StationDetail` — adds `up_next`, `recently_played` |
| `GET /api/stations/{slug}/artwork` | Station artwork image |
| `GET /api/stations/{slug}/listen` | Redirects to the live stream (rate-limited 20/min) |
| `GET /api/stations/{slug}/listen.m3u` | M3U playlist wrapper (rate-limited 10/min) |

`StationSummary` fields the app cares about: `slug`, `name`, `description`,
`artwork_url`, `on_air`, `listeners`, `stream_url` (the direct Icecast URL —
prefer this over the `/listen` redirect for the player), `stream_epoch`
(increments when the broadcast restarts; the web player uses it to cache-bust
reconnects — do the same on stream failure), and `now_playing`
(`title`, `artist`, `cover_url`, optional `artist_bio`, optional `knowledge`
trivia block).

**Now-playing metadata:** poll `GET /api/stations/{slug}` while tuned in (the web
UI polls on the order of every few seconds — check `web/static/app.js` for the
current interval and mirror it). Icecast in-stream metadata is not the source of
truth; the API is.

## iOS implementation notes

- **Playback:** `AVPlayer` with the station's `stream_url`. Live stream —
  duration is indefinite; hide/disable seeking. "Pause" should stop the stream
  and "play" should re-tune live (fresh connection), not resume a buffer.
- **Background audio:** enable the *Audio, AirPlay, and Picture in Picture*
  background mode; configure `AVAudioSession` category `.playback`. Handle
  interruptions (calls/Siri) and route changes (headphones unplugged) via the
  standard `AVAudioSession` notifications.
- **Lock screen / Control Center:** `MPNowPlayingInfoCenter` for title/artist/
  artwork, `MPRemoteCommandCenter` for play/stop. No skip commands.
- **Recovery:** on `AVPlayerItem` failure or stall, rebuild the player item with
  the stream URL (append `stream_epoch` or a timestamp as a query param to bust
  caches) and retry with backoff. This is the native replacement for the fragile
  PWA reconnect logic.
- **Server URL:** make the backend base URL user-configurable in a settings
  screen (self-hosted product — every user has their own server). Default to
  nothing; first-run asks for the URL. For development, point at your own
  server or a local `docker compose up` stack (`http://localhost:8080`, streams
  on `:8000`). Note: HTTP (non-TLS) servers need an App Transport Security
  exception in the Xcode project for local/dev use.

## Suggested stack

SwiftUI + Swift Concurrency (async/await), iOS 17+ target unless there's a reason
to go lower. No third-party dependencies needed for v1 — URLSession + Codable for
the API, AVFoundation for playback.

## Repo layout

Put the app in **`ios/`** at the repo root (e.g. `ios/AlchemyFM.xcodeproj`). Same
repo, so the app and backend API evolve together and Claude sessions always have
both in context. Backend/web code is untouched by iOS work.

## Suggested v1 milestones

1. Xcode project scaffold in `ios/`, builds and runs empty in the Simulator.
2. Station list screen fed by `GET /api/stations` (name, artwork, on-air badge,
   listener count).
3. Tune-in: tap a station → `AVPlayer` plays `stream_url`; audible in Simulator.
4. Now-playing screen with polling metadata (title/artist/cover, up next,
   recently played).
5. Background audio + lock-screen controls + interruption handling.
6. Stall/failure recovery with reconnect + backoff. **This is the whole reason
   the app exists — test it hard** (kill the network, restart a station mid-song,
   lock the phone for 10+ minutes).
7. Settings screen for server URL; polish (themes later, PWA has per-listener
   color themes).

## Development environment on the Mac

- Repo: `https://github.com/MMagTech/alchemyfm.git`
- You don't need the full Docker stack running on the Mac if a real Alchemy FM
  server is reachable on the network — the app is just an API + stream client.
- `docs/DEVELOPMENT.md` covers backend-only dev if a local server is needed.
- Simulator plays audio fine; background-audio and lock-screen behavior must
  ultimately be verified **on a physical iPhone** (free Apple ID personal team
  signing works for on-device dev builds).
