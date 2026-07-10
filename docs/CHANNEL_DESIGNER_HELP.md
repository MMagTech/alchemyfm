# Channel Designer Help

Operator guide for the **Alchemy FM Channel Designer** AudioMuse plugin (v3+).

Design stations in AudioMuse using CLAP, lyrics, mood clusters, and more — then deploy live programming to [Alchemy FM](https://github.com/MMagTech/alchemyfm).

---

## Quick answer: what goes where?

| Scope | Where | What it controls |
|-------|--------|------------------|
| **Universal (set once)** | **Plugins → Alchemy FM → Settings** | Alchemy FM URL, admin login, optional AudioMuse API URL/token for worker/cron |
| **Universal (enable once)** | **Administration → Scheduled Tasks → Alchemy FM** | Nightly living-channel refresh for **all** stations that have living enabled |
| **Per station** | **Channel Designer form** (one saved profile per slug) | Programming, filters, bootstrap opener, living toggles, name/slug/mount/queue rules |
| **Per deployed station** | **Edit toolbar** (top of designer when editing) | Refresh queue, rebuild pool, on/off air, artwork — without redeploying |
| **Not a station** | **Alchemy FM Admin** (`/admin.html`) | Encoding, themes, heart playlist, global broadcast — **not** programming |
| **Helpers only** | **Discover Channels**, **Chat designer** | Ideas and shortcuts — they prefill the designer; nothing deploys until you Preview + Deploy |

Everything in the big designer form is **per station**. When you pick or create a channel, you are editing **that channel's profile**. Filters, bootstrap, and living settings only affect the station you are building or editing — they are not global defaults.

---

## Create your first station (recommended flow)

Do this once for plugin setup, then repeat steps 2–7 for each new channel.

### 0. One-time setup

1. **Plugins → Alchemy FM → Settings** — Alchemy FM URL + `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
2. **(Optional)** AudioMuse API URL — LAN address the **worker** can reach (e.g. `http://192.168.1.10:8387`). Required for living auto-add and cron if the worker is not on the same host as the web UI.
3. **(If using living channels)** **Administration → Scheduled Tasks** — enable **Alchemy FM** (`plugin.alchemy_fm_bridge.refresh_living`).
4. **v3 upgrade** — Delete pre-v3 Alchemy FM stations and redeploy fresh. v3 uses live CLAP/lyrics/mood refills instead of frozen Song Alchemy anchors.

### 1. Open the designer

**AudioMuse → Alchemy FM** (Channel Designer).

On a **new** channel you see, top to bottom:

1. **Your Stations** — pick existing or **+ New channel**
2. **Discover Channels** — optional inspiration from clustering playlists
3. **Channel Designer** form — where you build the station
4. **Preview** / **Audition history** — after you run Preview

### 2. Choose how the station is programmed (required)

In **Programming**, pick **one** type and fill its field:

| Type | You set | Example |
|------|---------|---------|
| **Sonic Vibe (CLAP)** | Text query | `late-night yacht rock, smooth vocals` |
| **Lyrics Theme** | Text query | `songs about the open road` |
| **Mood Cluster** | Mood + cluster index | From your library analysis |
| **Song Alchemy Anchor** | Anchor id | Reuse an existing anchor playlist |
| **Similar to Seed Track** | One track id | Build around sonic neighbors |

This is the **core identity** of the station. Refills and cron re-run this query (v3 live mode).

**Optional shortcut:** **Discover Channels → Use in Designer** prefills a CLAP query. **Chat designer** is a slow LLM brainstorm — use it for ideas, then switch to a programming type and deploy.

### 3. Narrow the pool (optional — per station)

**Filters** trim which tracks count for **this station**:

- Preview results
- Living **auto-add** (new analyzed songs)
- Living **cron refresh**

Use filters when the programming query is broad but you want tighter BPM, energy, year, genre, mood, or artist rules. After **Preview Programming**, the **Filter check** panel shows which terms matched.

Filters do **not** change AudioMuse globally — only this channel profile.

### 4. Cold-start opener (optional — per station)

**Bootstrap Opener** — optional Navidrome playlist that plays **first** at deploy:

- Search → pick, or paste **Playlist ID** → **Verify**
- **Deploy** blocks if the playlist cannot be resolved in AudioMuse

Ongoing programming still comes from step 2. Bootstrap is only the opening sequence.

### 5. Living channel (optional — per station)

**Living channel** — evolve **this station** as your library grows:

| Checkbox | Effect |
|----------|--------|
| **Enable living channel** | Master switch for this slug's pool |
| **Auto-add** | New analyzed songs that pass **filters** join the pool |
| **Refresh** | Cron re-runs programming and can push updates to Alchemy FM |

Requires the global cron task (step 0) and worker-reachable API URL if needed.

### 6. Preview (required before deploy)

Click **Preview Programming** (bottom of **Channel + deploy** panel).

- Review tracks in the **Preview** table (BPM, energy, mood, genre)
- Fix programming or filters if the list is empty or wrong
- Bootstrap and filter checks run here (warnings if something fails)

Preview saves an audition for this channel; it does **not** create the radio station yet.

### 7. Deploy (creates the station)

Fill **Channel + deploy** (same form, bottom):

| Field | Notes |
|-------|--------|
| **Channel name** | Display name on Alchemy FM |
| **Slug** | Fixed after first deploy — identifies the station |
| **Description** | Short homepage blurb |
| **Icecast mount** | e.g. `/yachtrock` |
| **When pool runs low** | How refills drift (similar to last played, seed, or pool) |
| **Queue target / Refresh below / Artist separation** | Playback queue behavior on Alchemy FM |
| **Start on air after push** | Goes live immediately if checked |
| **Bootstrap queue immediately** | Fills queue on deploy |

Click **Deploy to Alchemy FM**. Listen on the mount; tune encoding and appearance in **Alchemy FM Admin**.

---

## Edit an existing station

1. **Your Stations** → **Edit** on the row (or open from the list at the top when editing).
2. The **Edit toolbar** appears: refresh queue, rebuild pool, on/off air, artwork.
3. Change programming, filters, bootstrap, or living in the form.
4. **Preview Programming** → **Save changes to Alchemy FM** (same as deploy for an existing slug).

Slug cannot change after first deploy.

---

## Page map (what each section is for)

```
┌─────────────────────────────────────────────────────────────┐
│  UNIVERSAL: Plugins → Settings (not on this page)            │
│  UNIVERSAL: Scheduled Tasks → Alchemy FM (living cron)      │
└─────────────────────────────────────────────────────────────┘

  NEW CHANNEL                         EDITING A STATION
  ───────────                         ─────────────────
  Your Stations                       Edit toolbar (ops)
  Discover Channels (helper)          Programming
  Channel Designer                    Chat designer (helper)
    ├ Programming        ◄── required per station
    ├ Chat designer      ◄── helper only
    ├ Filters            ◄── optional per station
    ├ Bootstrap opener   ◄── optional per station
    ├ Living channel     ◄── optional per station
    └ Channel + deploy   ◄── per station + Preview/Deploy
  Preview / Audition                  Your Stations (switch)
```

---

## Programming types (detail)

| Type | Use when |
|------|----------|
| **Sonic Vibe (CLAP)** | Describe the sound in plain language (e.g. late-night rock, energetic guitar). |
| **Lyrics Theme** | Search by meaning or theme (e.g. songs about the open road). |
| **Mood Cluster** | Pick a mood and sub-cluster from your library analysis. |
| **Song Alchemy Anchor** | Reuse an existing anchor playlist. |
| **Similar to Seed Track** | Build around one library track's sonic neighbors. |

---

## Filters (detail)

Optional rules narrow **preview**, **living auto-add**, and **cron refresh** for **this station only**:

- Tempo and energy bounds
- Year min/max
- Genre include/exclude — **exact match** on analyzed `top_genre` (see Genre column in Preview)
- Mood tags include — autocomplete from AudioMuse mood labels; inline warning for unknown tags
- Exclude artists — search library to add; exact match on artist name

After **Preview Programming**, the Filters panel shows a **Filter check** summary: which terms matched tracks in the pool and the top genres present.

---

## Bootstrap opener (detail)

Optional **Navidrome playlist** cold-start for **this station**:

- **Search** Navidrome playlists by name and **pick** a result (sets Playlist ID automatically), or paste an id manually.
- Click **Verify** to confirm AudioMuse can resolve the playlist and load opener tracks.
- On **Preview**, a bootstrap warning appears if verification fails; **Deploy** blocks until the playlist verifies.
- Opener tracks play first at deploy/bootstrap; refills use your programming query.

Playlist search uses AudioMuse `GET /api/search_playlists`; verification uses `GET /api/playlist`.

---

## Living channel (detail)

When enabled on **this** saved profile:

- **Auto-add** — newly analyzed songs that pass filters join the channel pool (`on_song_analyzed` on the worker).
- **Auto-refresh** — cron re-runs programming, merges the pool, and optionally updates the Alchemy FM station + queue.

Enable the cron task under **AudioMuse → Administration → Scheduled Tasks → Alchemy FM** (`plugin.alchemy_fm_bridge.refresh_living`, default 03:00 daily).

---

## Discover channels (helper)

Lists **clustering playlists** from AudioMuse `GET /api/playlists`. Not a station — a browse list.

- **Run Clustering** — optional manual `POST /api/clustering/start`.
- **Use in Designer** — prefills a CLAP query from the cluster name/mood.

Wait for clustering to finish (check **Active Tasks**) before expecting playlists to appear.

---

## Chat designer (helper)

Natural-language playlist ideas via `POST /chat/api/chatPlaylist`. Requires AudioMuse chat/AI configured.

- Slow (LLM) — use for initial ideas, then tweak programming and deploy.
- **Not** wired to living cron and **not** saved as programming until you deploy from the main form.

---

## Edit toolbar (deployed stations)

| Action | What it does |
|--------|----------------|
| **Refresh Queue** | Refill the play queue from programming + pool. |
| **Rebuild Pool** | Full bootstrap — clears queue/pool and re-imports. |
| **Rebuild M3U** | Rewrite `queue.m3u` from the database. |
| **Put On Air / Take Off Air** | Toggle `enabled` without redeploying. |
| **Upload / Remove Art** | Station artwork on Alchemy FM. |

**Source error** badges show the last programming/refill failure from Alchemy FM.

For encoding, themes, and heart playlist, use **Alchemy FM Admin** — not the plugin.

---

## When pool runs low

**Continuation mode** on deploy (per station):

| Mode | Behavior |
|------|----------|
| **Similar to Last Played** | Drift from the last track (recommended for variety). |
| **Similar to Programming Seed** | Stay near the fixed seed. |
| **Stay in Source Pool** | Reuse imported pool; allows repeats. |

---

## Cloudflare / public URLs

If Alchemy FM is behind Cloudflare, allow server-to-server access to `/api/admin/*` from your AudioMuse host, or use a direct/LAN URL in plugin settings.

---

## More

- Plugin catalog: [audiomuse-plugins/README.md](../audiomuse-plugins/README.md)
- Roadmap / backlog: [CHANNEL_DESIGNER_BACKLOG.md](CHANNEL_DESIGNER_BACKLOG.md)
- Alchemy FM admin: your instance `/admin.html`
