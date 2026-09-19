# Centurion

Two-person (Jermaine / Sophia) longevity dashboard: Oura sync, meal protocol, labs + InBody trends, and an AI coach backed by your own Ollama server. One file, no build step — `index.html` is the whole app and GitHub Pages serves it as-is.

## Run it locally

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Then open http://localhost:8765. (In Claude Code the `centurion` launch config does the same thing.)

## Private data never goes in this repo

The repo is public. Oura tokens, dates of birth, lab values and InBody history live in each device's `localStorage` and are entered through **⚙ Settings** in the app:

| What | Where it lives | Key |
|---|---|---|
| Oura token, DOB | ⚙ Settings, per user | `c_profile_<user>` |
| AI server URL, chosen models | ⚙ Settings / AI panel | `c_ai` |
| Labs + InBody | BIO screen | `c_bios_<user>`, `c_bioHist_<user>` |
| Meals, water, checks… | normal use | `c_*_<user>` |

**⚙ → Export backup** downloads every `c_*` key as one JSON file; **Import backup** restores it on another device. That file contains tokens — treat it like a password. `private/` is gitignored and holds `seed-backup.json`, the lab/InBody history that used to be hardcoded, in the same import format (use it to set up a brand-new device).

## AI coach

Talks straight from the browser to Ollama's `/api/chat`. Chat streams; "Log Meal" and "Scan" make one-shot `format: json` calls, and anything with a photo goes to the model picked in the 📷 dropdown (the app asks `/api/show` which models have the `vision` capability).

Two things the Ollama box must allow:

- **Origin** — Ollama rejects unknown web origins with 403. Set `OLLAMA_ORIGINS=https://jclarke916.github.io` (localhost origins are allowed by default) and restart Ollama.
- **HTTPS** — the GitHub Pages site is https, and browsers block https pages from calling `http://…`. Phones need an https URL for Ollama (e.g. a Tailscale Serve route); plain `http://<ip>:11434` only works from the local dev server.

`tailscale serve` can only proxy to localhost. If Ollama is bound to a single non-localhost address, `tools/ollama_localhost_forward.py <ip>` pipes `127.0.0.1:11434` to it without widening what Ollama listens on; then `tailscale serve --bg --https=8443 http://127.0.0.1:11434` gives phones on the tailnet an https URL to put in ⚙ Settings.

`python tools/mock_ollama.py` runs a fake Ollama on port 11435 for UI work without loading a model.

## Home sync (automatic)

Both phones keep their own full copy in `localStorage` and sync it with `server/sync_server.py`, a stdlib-only Python service on the home PC:

```bash
pythonw server/sync_server.py --data C:/Users/<you>/CenturionData --port 8792
tailscale serve --bg --https=10000 http://127.0.0.1:8792
```

- The server listens on localhost only; `tailscale serve` makes it reachable (https, tailnet-only) from the phones. Data is `<data>/state.json`, with a daily copy in `<data>/backups/` (60 kept) and activity in `sync.log`.
- In the app, **⚙ → Home Sync → Sync server URL**. A `#setup=<base64 {"sync": "...", "ai": "..."}>` link fills in both server URLs in one tap (the app asks first).
- Sync runs by itself: on open, ~1.5 s after any change, when the app comes back to the foreground, when the network returns, and every 20 s while open. The footer shows `☁ SYNCED <time>` / `☁ CAN'T REACH HOME`. Offline edits are kept and go up later.
- Unit of sync is one first-level property of one storage key (one day of meals, one lab field…), last write wins. `c_user`, `c_sections` and `c_ai` are per-device and never synced. Change detection is a diff against hashes of the last synced state (`csync_shadow`), so app code never has to call sync.
- **First sync of a device** trusts that phone for its current user's data and the server for everyone else's, so two phones with separate histories merge instead of overwriting each other. The phone's pre-merge data is kept in `csync_prefirst`.

## Oura proxy

Oura's API has no CORS headers, so requests go through a Cloudflare Worker (`OURA_PROXY` in `index.html`). `worker/oura-proxy.js` is the locked-down version: Oura URLs only, known origins only.

## Layout

- `index.html` — the app. `MEALS` / `USERS` / `PROTOCOLS` data, then `class CenturionApp` (everything on screen), then `class CenturionAI` (the 🤖 panel).
- `server/` — home sync server.
- `worker/` — Cloudflare Worker source.
- `tools/` — dev helpers.
