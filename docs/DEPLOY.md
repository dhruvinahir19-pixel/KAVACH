# P4 — Deployment Guide (Render free + cron-job.org + UptimeRobot)

**Goal:** the scanner runs on its own in the cloud. No laptop, no agent needed.
**Cost: ₹0.** (Render free 750 hrs/month — one small service fits; Neon free DB;
cron-job.org free; UptimeRobot free.)

---

## What you need (10 minutes of account setup)

1. **Render account** — https://render.com → "Sign in with GitHub" (use the same
   GitHub account that owns the KAVACH repo)
2. **cron-job.org account** — https://cron-job.org → register
3. **UptimeRobot account** — https://uptimerobot.com → register

---

## Step 1 — Put the scanner on Render

1. Render dashboard → **New + → Blueprint** → pick the **KAVACH** repository
   (render.yaml in the repo root is detected automatically)
2. It will ask for the environment variables. Fill these in:

| Variable | Value | Where to get it |
|---|---|---|
| `NEON_DATABASE_URL` | `postgres://...` (the whole string) | Neon dashboard → your project → Connection string (pick the **pooler** endpoint) |
| `TELEGRAM_BOT_TOKEN` | `8350939149:AAFn...` | the token @BotFather gave you |
| `TELEGRAM_CHAT_ID` | `6083434190` | your chat id |
| `TRIGGER_SECRET` | **make up** a long random text, e.g. 30+ characters with letters/numbers | you invent it — SAME value goes into cron-job.org later |
| `UPSTOX_ACCESS_TOKEN` | your Upstox token **or leave empty** | optional (backup data path) |
| `UPSTOX_TOKEN_GENERATED` | e.g. `2026-09-05` or empty | optional |

3. Click **Apply**. First build takes ~3–5 minutes.
4. When it is live, open `https://kavach-scanner.onrender.com/health`
   (your URL may differ — shown at the top of the service page).
   You must see: **OK**

---

## Step 2 — Set the timers on cron-job.org

Create **3 jobs** (all times **IST**, cron-job.org supports per-job timezone:
set each job's timezone to **Asia/Kolkata**):

| # | Title | Schedule (IST) | URL | Method |
|---|---|---|---|---|
| 1 | morning-wake | Mon–Fri 09:44 | `https://YOUR-URL/trigger/morning` | POST |
| 2 | morning-run | Mon–Fri 09:46 | `https://YOUR-URL/trigger/morning` | POST |
| 3 | evening | Mon–Fri 20:02 | `https://YOUR-URL/trigger/evening` | POST |

For **every** job:
- Advanced → **Request headers**: add `X-Trigger-Secret` = the SAME secret you
  invented on Render
- Advanced → Request timeout: leave 30s (jobs run in background; the HTTP
  response returns fast)
- Notifications → enable email on failure

Why two morning pings: Render free services sleep after 15 min idle; the 09:44
ping wakes the machine (cold start ~45–60s), the 09:46 ping does the real run.
If the service is already awake, the 09:44 ping is harmlessly too early
(the app answers "skipped:too-early" — the 09:40–09:45 candle isn't finished
before 09:45:40 anyway).

---

## Step 3 — Keep it awake (UptimeRobot)

1. Add **New Monitor** → type **HTTP(s)** → URL `https://YOUR-URL/health`
   → interval **5 minutes** → save.
2. This keeps the Render service from sleeping (free tier allows 744 running
   hours/month; the plan cap is 750 — it fits).

---

## Step 4 — Verify (the P4 gate)

Do these in order; all must pass before we call P4 done:

1. `https://YOUR-URL/health` → **OK**
2. Trigger selftest:
   `curl -X POST -H "X-Trigger-Secret: YOUR-SECRET" https://YOUR-URL/trigger/selftest`
   → expect `202` and a Telegram message from the bot
3. Manually trigger evening (before 23:00 IST on a trading day):
   same call with `/trigger/evening` → Telegram shows tonight's watchlist
4. Next trading morning: confirm the 09:46 cron fired (cron-job.org job history
   shows the call) and the Telegram signal arrived by ~09:47
5. Check the app logs on Render (service → Logs) for any red lines

---

## Failure cheat-sheet (what you'll see on Telegram)

| Message | Meaning | Action |
|---|---|---|
| `skipped:weekend` / `skipped:holiday` | normal — market closed | none |
| `skipped:too-early` | the 09:44 wake ping — normal | none |
| `bhavcopy missing after ladder` | NSE hadn't posted by 21:55 | check NSE site; we retry next day |
| `VIX unavailable` | VIX feed dead — no watchlist sent | tell the agent |
| `all morning fetches failed` | live data unavailable — NO signal sent (never a fake "flat") | check manually, tell the agent |
| Upstox token alerts | token expired/regenerated | paste new token in Render env vars |

---

## Notes

- **Never** put secrets in GitHub, files, or chat. Render's dashboard is the
  only place tokens live in production.
- Render free has no persistent disk — that's fine, ALL state lives in Neon.
- If Render suspends the service (out-of-hours bug on free tier), the Telegram
  alerts stop too — UptimeRobot email is the backup notification path.
