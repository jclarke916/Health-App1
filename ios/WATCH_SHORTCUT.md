# Apple Watch → Centurion with a Shortcut

Build this once on **each** iPhone (about 10 minutes). It reads today's numbers from Apple Health and posts them to your home sync server; both phones then get them through sync. Tailscale must be on.

Replace `SYNC-URL` with your sync server address and `NAME` with `jermaine` or `sophia` (lowercase) — the owner of **this** phone's watch.

## 1. Make the shortcut

Shortcuts app → **+** → name it **Centurion Watch Push**.

For each row below add **two actions**:

1. **Find Health Samples** — set *Type* as listed, then tap *Add Filter* → **Start Date** → **is today**.
2. **Calculate Statistics** — set the operation as listed; its input is the Health Samples from the step above.

Rename each Calculate Statistics result (tap the variable → *Rename*) so step 2 is easy to read.

| Rename result to | Find Health Samples → Type | Calculate Statistics |
|---|---|---|
| `move` | Active Energy | Sum |
| `exercise` | Exercise Minutes (Exercise Time) | Sum |
| `stand` | Stand Hours (Apple Stand Hour) | Sum |
| `steps` | Steps | Sum |
| `rhr` | Resting Heart Rate | Average |
| `hrv` | Heart Rate Variability | Average |

Optional, same pattern: `vo2max` (VO2 Max, Average), `resp` (Respiratory Rate, Average).

**Sleep (optional but worth it if your Oura is ever off):** Find Health Samples → Type **Sleep**, filter *Start Date is in the last 1 day* and *Value is Asleep* (on newer iOS pick the Core/Deep/REM values). Then **Calculate Statistics → Sum** on the samples' **Duration**, and rename to `sleepHours`. If Shortcuts gives you the duration in a unit other than hours, add a **Calculate** action to convert it to hours first. Skip this row if it fights you — everything else works without it.

## 2. Send it

Add **Get Contents of URL**:

- URL: `SYNC-URL/apple?user=NAME`
- Tap *Show More* → Method **POST** → Request Body **JSON**
- **Headers** → add one: Key `Authorization`, Value `Bearer ` followed by your household key (the key is in `private/phone-setup-link.txt` on the home PC, or in Centurion → Settings after the setup link). Without it the server answers `{"error": "household key required"}`. It works from anywhere, no Tailscale needed.
- Add one field per row above — Key = the name (`move`, `exercise`, `stand`, `steps`, `rhr`, `hrv`, …), Type **Text**, Value = the matching renamed variable.
- Optional fixed field: Key `moveGoal`, Type Text, Value = your Move goal (e.g. `600`).

Text is fine: the server pulls the number out of values like `540.2 kcal` or `8,412`, and ignores blanks (no samples yet today).

Run it once with ▶. The result should read `{"ok": true, "days": ["2026-…"], "changed": 1}`. Open Centurion — within ~20 seconds the **⌚ APPLE WATCH** row appears under the Recovery Battery. The first run asks for Health access for each type: allow them all.

## 3. Make it automatic

Shortcuts → **Automation** tab → **+**:

- **App** → choose **Centurion** (or Safari, if you use the web version) → *Is Opened* → **Run Immediately** → action: *Run Shortcut* → Centurion Watch Push.
- Add a second one: **Time of Day** → 9:30 PM, Daily → **Run Immediately** → same shortcut. This captures the day's final rings for the scoreboard even if you never open the app that evening.

## Notes

- Pushing again the same day just updates that day's numbers.
- **Workouts:** Oura already imports Apple Watch workouts when "Apple Health → Workouts" is enabled in the Oura app, and Centurion de-duplicates against them. The exercise-minutes ring also feeds the Training score, so workouts are not needed in the Shortcut.
- If the result says `user must be one of …`, the `?user=` part of the URL is missing or misspelled.
- If it cannot connect: is Tailscale on? Is the home PC awake?
