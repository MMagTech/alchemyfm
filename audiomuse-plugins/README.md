# AudioMuse plugins for Alchemy FM

Third-party [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) plugins maintained alongside the [Alchemy FM](https://github.com/mmagtech/alchemyfm) radio stack.

## Alchemy FM Bridge (`alchemy_fm_bridge`)

Design a station in AudioMuse and push it to a running Alchemy FM backend:

1. Install the plugin in AudioMuse (**Plugins → Repositories**).
2. Open **Alchemy FM Bridge → Settings** and set:
   - **Alchemy FM URL** — e.g. `http://192.168.1.10:8080`
   - **Admin username / password** — same as `ADMIN_USERNAME` / `ADMIN_PASSWORD` in Alchemy FM `.env`
3. Open **Alchemy FM** in the AudioMuse menu.
4. Pick a Song Alchemy anchor or search for a similar-seed track, then **Push to Alchemy FM**.

Pushes are **idempotent by slug**: if a station with the same slug already exists on Alchemy FM, the plugin updates it instead of creating a duplicate. Optional bootstrap fills the queue immediately after push.

### Local development / testing

AudioMuse can install plugins from a local catalog without publishing to the community repo:

```bash
cd audiomuse-plugins
zip -r alchemy_fm_bridge.zip alchemy_fm_bridge/__init__.py alchemy_fm_bridge/alchemy_client.py
python -m http.server 8000
```

1. Add `sourceUrl` to the `1.0.0` entry in `alchemy_fm_bridge/plugin.json` pointing at `http://<your-lan-ip>:8000/alchemy_fm_bridge.zip`.
2. In AudioMuse: **Plugins → Repositories → Add** `http://<your-lan-ip>:8000/manifest.json`.
3. Install from the Catalog tab and apply the restart.

Use a LAN IP the AudioMuse container can reach — not `localhost`.

### Publishing to the community catalog

To list in [NeptuneHub/AudioMuse-AI-plugins](https://github.com/NeptuneHub/AudioMuse-AI-plugins):

1. Fork that repo and add this plugin under `plugins/AlchemyFmBridge/`.
2. Run their build workflow to produce version zips and checksums.
3. Open a pull request per their [catalog policy](https://github.com/NeptuneHub/AudioMuse-AI-plugins#adding-a-plugin).

Alchemy FM itself needs **no code changes** for v1 — the plugin uses the existing admin API (`/api/admin/stations`).
