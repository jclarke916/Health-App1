# Centurion cloud sync (Cloudflare Worker)

Sync, the Oura link and the watch push used to live on the house PC. That PC is a laptop whose
sleep state is *S0 Low Power Idle — Network Disconnected*: press its power button and it leaves the
network, which is why the phones said **CAN'T REACH HOME** every morning. This Worker runs the same
service on Cloudflare, so the phones work whether the PC is awake, asleep or off.

The **AI coach stays on the PC** — it needs the GPU. It answers only while the PC is awake, at
`https://sophia.tail9f5f4a.ts.net:10000/ai`.

| | Where it runs | Needs the PC awake |
|---|---|---|
| Sync, Oura, watch push | Cloudflare | no |
| AI coach (chat, photo, InBody reader) | house PC | yes |

## Deploy

Run these in `worker/` on the PC. Everything except step 1 is safe to repeat.

```bash
npx wrangler login
```

```bash
npx wrangler d1 create centurion
```

Copy the `database_id` it prints into `wrangler.toml`, then create the tables:

```bash
npx wrangler d1 execute centurion --remote --file schema.sql
```

Set the three secrets (each prompts for the value — the household key is the one line in
`C:\Users\Scene\CenturionData\household_key.txt`, the Oura pair is in `oura_client.json`):

```bash
npx wrangler secret put HOUSEHOLD_KEY
```

```bash
npx wrangler secret put OURA_CLIENT_ID
```

```bash
npx wrangler secret put OURA_CLIENT_SECRET
```

Move the existing data and Oura links across (keeps the same server id, so the phones just carry on,
and nobody has to reconnect Oura):

```bash
python ..\tools\export_to_cloud.py
```

```bash
npx wrangler d1 execute centurion --remote --file ..\private\migrate.sql
```

```bash
npx wrangler deploy
```

`wrangler deploy` prints the URL (`https://centurion-cloud.<your-subdomain>.workers.dev`). That URL
goes into the app as the sync address, and into the Oura app's redirect list if it ever changes.

## Test it

```bash
curl -H "Authorization: Bearer <household key>" https://centurion-cloud.<subdomain>.workers.dev/health
```

Expect `{"ok":true,"rev":…,"entries":…,"where":"cloudflare","oura":{…}}`. Without the header, the
same URL returns only `{"ok":true}` — that is deliberate: an unknown caller learns nothing.

## Run it locally

```bash
npx wrangler d1 execute centurion --local --config wrangler.local.toml --file schema.sql
```

```bash
npx wrangler dev --local --config wrangler.local.toml --port 8799
```

`wrangler.local.toml` (a copy of `wrangler.toml` with a placeholder database id) and `.dev.vars`
(test secrets) are both gitignored. The suite in `tools/test_cloud_worker.py` runs against it.

## What is stored

* `meta` — `serverId` and `rev`, the same two values the PC server kept.
* `entries` — one row per (storage key, first-level property), the unit the app syncs. Last write
  wins; deletes are tombstones so a delete travels between phones.
* `oura` — one row per person: access token, refresh token, expiry. The client id and secret are
  Worker secrets, never rows, and never reach a phone.

Every route needs `Authorization: Bearer <household key>`. The only AI route the old server had is
**not** here; nothing in this Worker can reach Ollama.
