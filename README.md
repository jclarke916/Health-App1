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

## Oura proxy

Oura's API has no CORS headers, so requests go through a Cloudflare Worker (`OURA_PROXY` in `index.html`). `worker/oura-proxy.js` is the locked-down version: Oura URLs only, known origins only.

## Layout

- `index.html` — the app. `MEALS` / `USERS` / `PROTOCOLS` data, then `class CenturionApp` (everything on screen), then `class CenturionAI` (the 🤖 panel).
- `worker/` — Cloudflare Worker source.
- `tools/` — dev helpers.
