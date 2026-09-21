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

- **No VPN on the phones.** `tailscale funnel --bg --https=10000 http://127.0.0.1:8792` publishes this one port to the internet, so it is locked with a **household key**: put a long random string in `<data>/household_key.txt` and every route (except a bare `/health`) demands `Authorization: Bearer <key>`. Without the file, auth is off (tailnet-only use). The key reaches a phone inside the setup link's `#fragment` (never sent to a server) and is stored per device in `csync_key`. The app only ever sends it to `HOME_HOST` over https.
- The AI coach goes through the same port at `/ai/…`, which proxies to Ollama on `127.0.0.1:11434`. Only chat, show, tags and version are allowed, so nobody can pull or delete models. Chat replies start at once with a blank keep-alive line every 5 s while a model loads, because iOS drops connections that stay silent for about 60 s.
- The server listens on localhost only; Tailscale forwards to it. Data is `<data>/state.json`, with a daily copy in `<data>/backups/` (60 kept) and activity in `sync.log`.
- In the app, **⚙ → Home Sync → Sync server URL**. A `#setup=<base64 {"sync": "...", "ai": "...", "key": "..."}>` link (or `centurion://setup#…` for the iOS app) fills in both server URLs and the household key in one tap (the app asks first).
- Sync runs by itself: on open, ~1.5 s after any change, when the app comes back to the foreground, when the network returns, and every 20 s while open. The footer shows `☁ SYNCED <time>` / `☁ CAN'T REACH HOME`. Offline edits are kept and go up later.
- Unit of sync is one first-level property of one storage key (one day of meals, one lab field…), last write wins. `c_user`, `c_sections` and `c_ai` are per-device and never synced. Change detection is a diff against hashes of the last synced state (`csync_shadow`), so app code never has to call sync.
- **First sync of a device** trusts that phone for its current user's data and the server for everyone else's, so two phones with separate histories merge instead of overwriting each other. The phone's pre-merge data is kept in `csync_prefirst`.

## Apple Watch / Apple Health

A web page cannot read HealthKit, and the iOS shell is an App Playground (no HealthKit entitlement), so the phone **pushes** watch data to the sync server and both phones receive it through sync as `c_apple_<user>`:

```
POST https://<sync-server>/apple?user=jermaine        (or sophia)
{"days": {"2026-09-18": {"move": 520, "moveGoal": 600, "exercise": 42, "stand": 10, "steps": 8412,
  "rhr": 52, "hrv": 61, "vo2max": 44.1, "resp": 14.2,
  "sleep": {"totalSec": 25200, "deepSec": 4200, "remSec": 5400, "bedtime": "<iso>", "waketime": "<iso>"},
  "workouts": [{"type": "Strength", "start": "<iso>", "duration": 48, "calories": 390, "avgHr": 128}]}}}
```

Every field is optional; later pushes for the same day update it, workouts de-duplicate by start time. Two ways to send it, neither needing Xcode: an iOS **Shortcuts** automation ("Find Health Samples" → "Get Contents of URL", e.g. run when Centurion opens), or the **Health Auto Export** app's REST automation pointed at the same URL — its native JSON (`{"data": {"metrics": [...], "workouts": [...]}}`) is parsed too (mapping written from its documented format, not yet tried against the real app).

In the app Oura stays primary. The watch fills in missing sleep / RHR / HRV / respiratory rate, adds the rings row under the Recovery Battery, and adds workouts Oura did not already import (matched within 20 minutes).

## The Pact (accountability)

Each day each person commits to **3 things**; the app verifies what it can and the other person sees everything.

- **Commit** — ⚙-free: the Pact card's **SET MY 3**. Suggestions come from the data (low HRV → earlier bedtime + easy Zone 2; short sleep → bedtime; protein/fiber averaging under goal). Catalog: bedtime, sleep hours, Zone 2, workout, steps, protein, fiber, water, omega-3, no alcohol, no added sugar, custom.
- **Auto-verify** — bedtime/sleep against Oura, Zone 2/steps/workouts against Oura or the watch, food against logged meals, water against the water log. **Oura files a night under the day you wake**, so "in bed by 10:30 tonight" on day D is read from D+1's record, and bedtimes are compared as timestamps (a 12:40am bedtime is a miss, not "early").
- **Check in** — yes/no on what the data can't see; optional reason on anything. Unanswered self-report items on a past day count as missed. Late check-in allowed for 2 days; commitments can only be set for today or later.
- **Visibility** — the partner sees commitments, results and reasons, and can nudge (💪 🛏️ 💧 👏).
- **Streak: never miss twice** — a day is won at 2 of 3 kept; one missed day is forgiven, two in a row breaks it. Replaces the old all-6-meals rule that could never grow.
- **Week (Mon–Sun)** — the **team** wins if together you keep 80% of what you committed; whoever kept the smaller share owes the **stake** (ties fall back to the scoreboard).

Storage (all synced): `c_commit_<user>` (items + check-ins), `c_verify_<user>` (computed results, written by whichever phone is showing that user, never downgrading a settled day), `c_nudges`, `c_pact` (stake per week). The AI coach's context includes the day's pact.

## My Dishes and added ingredients

- **＋ New dish** at the top of every meal picker: name, meal, ingredients (one per line), and macros typed in or filled by **✨ Estimate with AI**. Saved to `c_dishes`, shared by both users and synced; listed under **★ My Dishes** above the built-in library, with ✎ Edit.
- **＋ Add an ingredient** in any dish: e.g. "1 tbsp olive oil" is AI-estimated and attached to that meal for that day. It rides the existing side-items mechanism (`sideItems`, `pool:'custom'`), so every total already counts it.
- **★ Save as dish** on meals logged through the AI panel.

A built-in dish is remembered by its **position** in `MEALS[slot]`; a custom one by a permanent id (`d_…`). Positions would shift when a dish is deleted and re-point past days at the wrong meal, so every lookup goes through `dishAt(slot, key)` and deleting only hides a dish — past days that used it still resolve. Macro keys follow the app convention: `p` protein g, **`f` fiber g**, `o` omega-3 mg, `cal`, `poly` mg (plus `fat`/`carbs` for display).

## Scoreboard

Daily points out of 100, each person measured against **their own** goals: Sleep 20, Training 20 (10 for a logged workout + 10 for Zone 2 / exercise minutes), Protein 15, Protocol checklist 15, Fiber 10, Water 10, Omega-3 5, Recovery (sauna / cold) 5. A phone scores the profile it is showing (that is where that person's Oura / watch data is) and saves `c_points_<user>`, which sync shares; points already earned are never erased just because wearable data has not loaded.

- **Day**: higher total wins once the day is over.
- **Week** (Mon–Sun): total points; the card also shows days won.
- **Month**: decided by **InBody** — last scan in the month vs. the scan before it: 10 pts per 1.0 of body-fat % lost, 10 pts per 1 % of muscle gained, 2 pts per InBody score point. Relative measures, so two different bodies compete fairly. Until both have a scan that month it shows the running points instead.

The rules live in `pointsRules()` / `scoreDay()` / `inbodyMonth()` in `index.html`.

## Oura (OAuth2)

Oura retired Personal Access Tokens in 2026 - new ones cannot be created and existing ones are
being switched off - so access is OAuth2 against a registered application. The client secret
cannot live in a public repo, so the sync server holds it:

```
<data>/oura_client.json    {"client_id": "...", "client_secret": "..."}   <- you create this
<data>/oura_tokens.json    written by the server, per user
```

Registered redirect URI: `https://jclarke916.github.io/Health-App1/oura.html`. Scopes: `daily workout`.

Flow: **⚙ Settings → CONNECT OURA** sends the browser to Oura with a `state` of `<user>.<nonce>`;
Oura returns to `oura.html`, which checks the nonce it stored and posts the code to
`POST /oura/exchange`; the server swaps it for tokens and keeps them. The app then reads data
through `GET /oura/data?user=&endpoint=&start=&end=`, which attaches the bearer token and
refreshes it when expired. **No Oura credential is ever stored on a phone.** The endpoint name
is checked against an allowlist. A still-valid legacy token in Settings is used as a fallback
until Oura switches it off.

## Oura proxy (legacy)

Oura's API has no CORS headers, so requests go through a Cloudflare Worker (`OURA_PROXY` in `index.html`). `worker/oura-proxy.js` is the locked-down version: Oura URLs only, known origins only.

## Layout

- `index.html` — the app. `MEALS` / `USERS` / `PROTOCOLS` data, then `class CenturionApp` (everything on screen), then `class CenturionAI` (the 🤖 panel).
- `server/` — home sync server (+ Apple Health push endpoint).
- `ios/` — Swift Playgrounds app package for TestFlight (see `ios/README.md`).
- `worker/` — Cloudflare Worker source.
- `tools/` — dev helpers.
