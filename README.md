# Swish Assist

**An AI support and ordering agent for Swish, on WhatsApp. It resolves a missing item from a photo in about 30 seconds, not 30 minutes.**

> Unofficial concept by Mohammed Zaid. Not affiliated with Swish. No Swish logos, brand assets or real customer data are used.

## What this is and why

Swish delivers food in 10 minutes from its own kitchens in HSR Layout. App reviews keep repeating the same complaints:

- wrong or missing items,
- support that is slow or never replies,
- delivery times that can't be trusted,
- coupons that confuse people.

Today, a human agent reads every complaint and decides each refund by hand.

Swish Assist does three jobs:

1. **Photo complaint, resolved in seconds (the hero feature).** The customer says "my fries are missing" and sends a photo. A vision model compares the photo with the order. Fraud checks run. Small, confident cases are refunded instantly. Everything else goes to a human with a summary they can act on in 10 seconds.
2. **Honest order status.** "Where is my food?" gets the real stage, the ETA, and the reason for any delay, in plain words.
3. **Reorder my usual with the best coupon.** "Same as usual" or "same as last Friday" finds the right past order, checks every coupon's rules, shows the total, and places the order after a yes.

It works on WhatsApp (Twilio) and in a web chat that looks like WhatsApp, so anyone can try it from a link. A live support dashboard shows the other side.

## Architecture in 30 seconds

```
WhatsApp (Twilio) ──┐
                    ├─> agent.py ── Groq gpt-oss-120b, tool calling (max 6 rounds)
Web chat /api/chat ─┘        │
                             ├─ get_order_status / get_recent_orders / get_usual_order
                             ├─ apply_best_coupon / place_order
                             ├─ check_photo_complaint ──> Groq vision model (JSON mode)
                             │        └─ fraud.py: duplicate photo hash, timing, refund rate, EXIF, photo mismatch
                             └─ escalate_to_human
                db.py: Supabase Postgres, or SQLite when SUPABASE_URL is empty
                simulator.py moves live orders through their stages · SSE pushes updates to the dashboard
```

- **The chat model never sees images.** An uploaded photo becomes `img_123`. Only `check_photo_complaint` sends it to the vision model, after resizing it to 1280 px JPEG under 3.5 MB.
- **Every decision rule lives in one pure function**, `decide()` in [app/tools/complaints.py](app/tools/complaints.py), with a unit test for each branch.
- **The provider layer is thin.** [app/llm.py](app/llm.py) has `chat_with_tools` and `vision_json`, with retries on 429/5xx and an automatic model fallback. To swap providers, change that one file.
- **No API key? It still runs.** The agent drops to a rule-based router and the vision step recognises the bundled sample photo, so every page works offline.

## Run it locally (3 commands)

```bash
pip install -r requirements.txt
cp .env.example .env        # add your GROQ_API_KEY
uvicorn app.main:app --reload
```

Open http://localhost:8000. The database is created and seeded on first start. The best view for a demo is http://localhost:8000/demo: chat on the left, dashboard on the right.

Other useful commands:

```bash
python scripts/seed.py --reset                  # wipe and reseed (also: "Reset demo" button)
python -m app.agent --user priya                # chat in the terminal
python -m app.agent --user sneha --image static/samples/burger_no_fries.jpg
```

Demo users: **Priya** (live order delayed by rain, usual = cold coffee + chicken popcorn), **Rahul** (weekday lunch), **Ananya** (Kannada), **Arjun** (Hinglish), **Sneha** (order delivered 10 minutes ago: fries, burger, cold coffee), **Vikram** (refunds on 5 of his last 8 orders).

## The decision rules

The checks run in two stages, cheapest first:

1. **Cheap checks, before any model call:** duplicate photo hash, complaint timing, refund history, EXIF date. If any of them flags the case, the complaint is escalated straight away with those flags in the summary and **the vision model is never called**. The dashboard card shows "vision skipped, flagged early", and the "Vision calls saved" tile counts these cases. The human reviewer still sees the value of the items the customer named, so Approve refunds that amount.
2. **Vision check, then the checks that need it** (not a food photo, photo doesn't match the order).

| Situation | Decision |
|---|---|
| A cheap check flags (duplicate photo, complaint before delivery or 3+ hours after, refund rate over 30% of the last 10 orders, EXIF date older than the order) | Escalate immediately, vision skipped |
| Vision check failed or returned broken JSON twice | Escalate to a human |
| Vision runs and a content check flags (not a food photo, photo doesn't match the order) | Escalate, with a summary |
| Overall confidence below `AUTO_REFUND_MIN_CONFIDENCE` (0.8) | Escalate |
| Nothing missing or wrong, confident | `no_issue_found`: reply with what was seen, offer a human |
| Only a quality issue (spilled, burnt), no fixed value | Escalate |
| Refund value above `AUTO_REFUND_MAX_INR` (₹300) | Escalate |
| Customer asked for a replacement and the kitchen is under 1 km away | `auto_replace` |
| Otherwise | `auto_refund` for the value of the missing items |

One more safety rule: if the customer names an item ("my fries are missing"), only that item can be refunded. Anything else the photo can't see is noted for the human but not refunded.

Every complaint is saved with the vision model used, its latency, the raw vision result, the fraud flags and `resolution_seconds`, measured from the customer's first complaint message to the decision.

## Connect WhatsApp (Twilio sandbox)

1. Create a free Twilio account. Open **Messaging → Try it out → Send a WhatsApp message**, and join the sandbox from your phone by sending the join code to the sandbox number.
2. Put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886` in `.env`.
3. Expose your local server:
   ```bash
   ngrok http 8000
   ```
4. Set `PUBLIC_BASE_URL` to the ngrok https URL and restart. Signature validation uses it.
5. In the sandbox settings, set **When a message comes in** to `https://<your-ngrok-id>.ngrok-free.app/whatsapp` (method POST).
6. Send "where is my food" from WhatsApp. A new number becomes a new user and gets a copy of Sneha's just-delivered order (fries, burger, cold coffee). That means you can try the photo flow right away: send any food photo with "my fries are missing".

Notes:
- Twilio waits 15 seconds for the webhook. If an answer takes longer, for example because of a rate-limit retry, the bot replies "One sec, checking…" and pushes the real answer through the Twilio API.
- Approve or reject decisions from the dashboard are also pushed to WhatsApp users.

## Tests and the eval

```bash
pytest                                   # 80+ tests, no API keys needed (Groq is mocked)
python scripts/eval_photos.py            # photo checker accuracy on test_images/
python scripts/eval_photos.py --compare  # primary vs fallback vision model
```

The eval reads `test_images/labels.json`. Each case pairs a photo with an order and lists the items that should be flagged as missing or wrong. It prints per-case results, precision and recall, average latency and the decision mix, and saves `eval_results.json`. The landing page then shows the headline numbers. On the Groq free tier it waits 20 seconds between cases because of token-per-minute limits; use `--delay 0` on a paid tier.

## Deploy to Render

1. Push this repo to GitHub and create a **Blueprint** on Render from it. `render.yaml` defines one web service.
2. Set `GROQ_API_KEY` (and optionally the Twilio and Supabase variables) in the Render dashboard.
3. Set `PUBLIC_BASE_URL` to your Render URL, for example `https://swish-assist.onrender.com`.
4. In the Twilio sandbox, set the webhook to `https://swish-assist.onrender.com/whatsapp`.

On startup the app creates its tables and seeds the demo data if the database is empty. With no Supabase variables it uses SQLite on the instance disk. That's fine for a demo, but it resets when Render restarts the instance.

**Using Supabase instead:** run [sql/schema.sql](sql/schema.sql) once in the Supabase SQL editor, then set `SUPABASE_URL` and `SUPABASE_KEY` (the service role key, kept on the server only). The app seeds it on first start.

To keep the demo working hours after a deploy, the simulator re-anchors the seeded demo orders (Sneha's and Vikram's delivered orders, Priya's rain-delayed order) to the current time, as long as nobody has complained about them yet. Anything else is never touched.

## What I'd build next if this were real

- **Connect to the real order system.** Read orders, kitchen events and rider GPS from Swish's backend instead of the simulator, and credit refunds through the real wallet API.
- **A kitchen camera check at packing time.** The same vision model, run on a photo of every bag before it leaves the kitchen. Most "missing item" complaints get prevented instead of refunded, and the packing photo becomes evidence for disputes.
- **A rider photo at delivery.** Proof of what was handed over, for the "before delivery" and "duplicate photo" fraud cases.
- **Learn the thresholds from data.** Tune the confidence and value caps per item and per customer from human review outcomes, and track the cost of wrong auto-refunds against agent time saved.
- **Proactive delay messages.** When the simulator, or the real system, sees a delay, message the customer before they ask.
- **Voice notes.** Groq Whisper is available on the same account, so a Kannada or Hinglish voice note could be handled like text.
