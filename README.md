# Swish Assist

**An AI support and ordering agent for Swish. It resolves a missing-item complaint from a photo in seconds, not 30 minutes.**

> Unofficial concept by Mohammed Zaid. Not affiliated with Swish. No Swish logos, brand assets or real customer data are used.

### ▶ Try it: **https://swish-assist.onrender.com/demo**

No sign-up or install needed. The page shows a WhatsApp-style customer chat on the left and the support team's live dashboard on the right. The [step-by-step test guide](#test-it-step-by-step-about-5-minutes) is below.

---

## What it does

Swish delivers food in 10 minutes from its own kitchens in HSR Layout. App reviews keep repeating the same complaints: wrong or missing items, slow support, delivery times that can't be trusted, and confusing coupons. Today a human agent reads every complaint and decides each refund by hand.

Swish Assist does three jobs:

1. **Photo complaint, resolved in seconds.** The customer says "my fries are missing" and sends a photo. Fraud checks run first. A vision model compares the photo with the order. Small, confident cases are refunded instantly. Anything else goes to a human, with a summary they can act on in 10 seconds.
2. **Honest order status.** "Where is my food?" gets the real stage, the ETA and the reason for any delay, in plain words.
3. **Reorder "my usual" with the best coupon.** It finds the right past order, checks every coupon's rules, shows the total and places the order only after the customer says yes.

It answers in English, Hinglish and Kannada.

---

## Test it step by step (about 5 minutes)

Open **[/demo](https://swish-assist.onrender.com/demo)**.

- **Chat panel (left):** pick a customer from the **Demo user** dropdown, then type a message or tap a quick-reply chip.
- **Dashboard panel (right):** updates live as you chat.
- **Before you start, click "Reset demo"** (top of the chat). It puts every customer and order back to the starting state. Click it again whenever you want to start over.

### The demo customers

| Customer | Their situation | Good for testing |
|---|---|---|
| **Priya** | Has an order on the way, delayed by rain. Usually orders cold coffee + chicken popcorn. | Order status, reorder with coupon |
| **Sneha** | Order delivered 10 minutes ago: fries, burger, cold coffee | Photo complaint → instant refund |
| **Vikram** | Got refunds on 5 of his last 8 orders | Fraud detection |
| **Arjun** | Writes in Hinglish | Language |
| **Ananya** | Prefers Kannada | Language |
| **Rahul** | Weekday lunch regular | Order status, reorder |

### Test 1: Where is my food?

| Step | What you should see |
|---|---|
| Pick **Priya** and tap **Where is my food** | A status card with a progress bar: *out for delivery*, ETA about 6 minutes, and the reason for the delay (heavy rain) |
| On the dashboard, find Priya's order under **Live orders** and click **Next ›**, then ask again | The order has moved to the next stage |

### Test 2: Reorder "my usual" with the best coupon

| Step | What you should see |
|---|---|
| Still as **Priya**, tap **Order my usual** | A cart card: Cold Coffee + Chicken Popcorn, coupon **HUNGRY20** saves ₹49, total **₹199** |
| Open **Why not other coupons** on the card | Why each other coupon doesn't apply (expired, already used twice, minimum order not met…) |
| Tap **Confirm order** | Order placed with a new order number, and it appears under **Live orders** on the dashboard |

Nothing is ordered until the customer says yes.

### Test 3: Photo complaint, refunded instantly (the main feature)

| Step | What you should see |
|---|---|
| Pick **Sneha**, click **Use sample photo** (a burger with no fries), type `my fries are missing` and send | Within a few seconds: *"Your Classic Fries is missing. ₹99 refunded to your Swish wallet."* |
| Look at the dashboard | A new card at the top of the feed with the photo, what the AI saw (with confidence bars) and the time it took. The tiles for complaints, refunds and resolution time update. |

### Test 4: Fraud caught, before any AI call

| Step | What you should see |
|---|---|
| Pick **Vikram**, click **Use sample photo** (the same photo Sneha just used), type `my fries are missing` and send | *"I've passed this to our support team…"*. No refund. |
| Look at the dashboard | Vikram's card is marked **Escalated** with red badges **Duplicate photo** and **High refund rate**, plus **⚡ vision skipped, flagged early**. The **Vision calls saved** tile goes up by 1. |
| In the **Escalation queue**, click **Approve** (or **Reject**) | Vikram's chat gets a message from the "Swish support team" with the outcome |

Test 4 shows the fraud checks running *before* the AI looks at the photo. A reused photo is caught for free, and the AI model is never called.

### More things to try

Use the 📎 button next to the message box to upload your own photo.

| Try this | What should happen |
|---|---|
| **Sneha** (after Reset demo): upload a photo that isn't food, e.g. a keyboard, with `my fries are missing` | Escalated with **Not a food photo** |
| **Sneha**: upload an old photo from your gallery (taken days ago) | Escalated with **Old photo (EXIF)**: the photo was taken before the order existed |
| **Priya**: upload any photo with `fries missing in order SW7001` | Escalated with **Before delivery**: that order hasn't arrived yet |
| **Arjun**: `bhai mera order kahan hai` | Reply in Hinglish |
| **Ananya**: `ನನ್ನ ಆರ್ಡರ್ ಎಲ್ಲಿದೆ` | Reply in Kannada |
| Anyone: `I want to talk to a human` | Handed to the support team; appears in the escalation queue |

Separate pages: [/chat](https://swish-assist.onrender.com/chat) is the chat alone (works well on a phone), [/dashboard](https://swish-assist.onrender.com/dashboard) is the dashboard alone, and [/](https://swish-assist.onrender.com/) is the overview with accuracy numbers.

### If something looks off

- **A reply takes 10–20 seconds.** The demo runs on Groq's free tier, which limits requests per minute. Wait about 20 seconds between photo complaints. Nothing is broken; the bot says "One sec, checking…" and retries.
- **Results don't match the tables above.** Someone else may have tested before you. Click **Reset demo**.
- **Vikram's photo went to the vision model.** Sneha's complaint must come first, so the photo is already on record. Reset and run Test 3 before Test 4.

---

## How it works

```
Web chat (/chat, /demo) ──> agent.py ── Groq gpt-oss-120b, tool calling (max 6 rounds)
                                │
                                ├─ get_order_status / get_recent_orders / get_usual_order
                                ├─ apply_best_coupon / place_order
                                ├─ check_photo_complaint
                                │     1. cheap fraud checks: duplicate photo, timing, refund history, EXIF date
                                │     2. only if they pass: Groq vision model (JSON mode) + content checks
                                │     3. decide(): refund, replace, no issue, or escalate
                                └─ escalate_to_human
               db.py: SQLite (or Supabase Postgres when configured)
               simulator.py moves live orders through their stages · live updates push to the dashboard
```

- **The chat model never sees images.** An uploaded photo becomes an id like `img_123`. Only the photo-check tool sends it to the vision model, resized to 1280 px.
- **Every decision rule lives in one function**, `decide()` in [app/tools/complaints.py](app/tools/complaints.py), with a test for each rule.
- **One file talks to the AI provider:** [app/llm.py](app/llm.py). It handles retries and switches to a backup model when one is busy.
- **It still works without an API key.** A rule-based fallback answers the chat, and the bundled sample photo is recognised, so every page works offline.
- The agent can also sit behind real WhatsApp: a webhook is included in [app/whatsapp.py](app/whatsapp.py).

### The decision rules

Checks run cheapest first:

| Situation | Decision |
|---|---|
| A cheap check flags it: same photo used before, complaint before delivery or 3+ hours after, refunds on over 30% of the last 10 orders, photo taken before the order | Escalate immediately; the vision model is not called |
| The vision check fails | Escalate |
| Not a food photo, or none of the ordered items visible | Escalate |
| AI confidence below 80% | Escalate |
| Everything ordered is visible | "No issue found"; offer a human |
| Only a quality problem (spilled, burnt) | Escalate |
| Refund above ₹300 | Escalate |
| Customer wants a replacement and the kitchen is under 1 km away | Send a replacement |
| Otherwise | Refund the missing items instantly |

Only items the customer names can be refunded automatically. If they say "fries" and the photo also doesn't show the coffee, the coffee is noted for a human but not refunded.

Every complaint records the vision model used, its response time, what it saw, the fraud flags and the total time from the customer's first message to the decision.

---

## Run it on your own machine

You need Python 3.11+ and a free Groq API key from [console.groq.com/keys](https://console.groq.com/keys). Without a key it still runs, in offline mode.

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then open .env and paste your key after GROQ_API_KEY=
uvicorn app.main:app --reload
```

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then paste your key after GROQ_API_KEY=
uvicorn app.main:app --reload
```

Open **http://localhost:8000/demo** and follow the [test guide](#test-it-step-by-step-about-5-minutes). Demo data is created automatically on first start.

Handy extras:

```bash
python scripts/seed.py --reset                     # wipe and reload demo data (same as "Reset demo")
python -m app.agent --user priya                   # chat with the agent in the terminal
python -m app.agent --user sneha --image static/samples/burger_no_fries.jpg
```

### Automated tests (no API key needed)

```bash
pytest          # 90+ tests, about 10 seconds; the AI is replaced by a scripted fake
pytest -v       # see every test by name
```

| Test file | What it checks |
|---|---|
| `test_coupons.py` | Every coupon rule: expiry, days and hours, minimum order, first order only, per-user limits, best coupon chosen |
| `test_orders.py` | Order status and ETAs, delays, "my usual" matching, the order simulator |
| `test_decision.py` | Each refund / escalate rule |
| `test_fraud.py` | Each fraud check on its own |
| `test_pipeline.py` | Cheap checks run first and skip the vision call when they flag |
| `test_llm.py` | Retries, backup model, image resizing, broken AI output |
| `test_api.py` | Full flows over HTTP: status, refund, fraud escalation, approve / reject |
| `test_keepalive.py` | The keep-awake ping |

### Photo checker accuracy

```bash
python scripts/eval_photos.py            # needs GROQ_API_KEY; about 5 minutes on the free tier
```

This runs the real vision model on the labeled photos in `test_images/` and scores how often it finds the right missing items. Last run: **14 of 14 cases correct**, median about 2 seconds per photo. To add cases, drop a photo in `test_images/` and describe it in `test_images/labels.json`.

---

## Deploy your own copy (Render, free)

1. Fork this repo, then on [render.com](https://render.com) choose **New → Blueprint** and pick the repo. `render.yaml` sets everything up.
2. When asked, paste your `GROQ_API_KEY`. Leave the other fields empty.
3. Open `https://<your-app>.onrender.com/demo`.

**Keeping it awake:** Render's free plan sleeps after 15 idle minutes, and the next visitor waits about a minute. The app prevents this itself by pinging its own `/health` every 10 minutes ([app/keepalive.py](app/keepalive.py)). As a backup after deploys, the GitHub Actions job in [.github/workflows/keep-alive.yml](.github/workflows/keep-alive.yml) pings it too. Turn it on by adding a repository variable `APP_URL` = your Render URL (Settings → Secrets and variables → Actions → Variables).

**Data:** by default the app uses SQLite on the server's disk. The demo data reloads automatically when Render restarts. To keep data permanently, run [sql/schema.sql](sql/schema.sql) in a Supabase project and set `SUPABASE_URL` and `SUPABASE_KEY`.

---

## What I'd build next if this were real

- **Connect to the real order system.** Read orders, kitchen events and rider GPS from Swish's backend, and credit refunds through the real wallet.
- **Kitchen camera check at packing time.** Run the same vision model on a photo of every bag before it leaves. Most missing-item complaints get prevented instead of refunded.
- **Rider photo at delivery.** Proof of what was handed over, for disputed cases.
- **Learn the thresholds from data.** Tune confidence and refund caps from human review outcomes.
- **Proactive delay messages.** Tell the customer about a delay before they ask.
- **Voice notes.** Handle Kannada or Hinglish voice notes the same way as text.

---

<sub>Unofficial concept by Mohammed Zaid. Not affiliated with Swish.</sub>
