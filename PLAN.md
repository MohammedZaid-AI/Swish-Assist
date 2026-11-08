# Swish Assist – Build Plan

Unofficial concept demo: an AI support + ordering agent for Swish (10-minute food delivery, HSR Layout).

## Architecture (one FastAPI process)

```
WhatsApp (Twilio) ─┐                      ┌─ tools/orders.py     (status, recent, usual)
                   ├─> agent.py (Groq ────┼─ tools/reorder.py    (cart, coupons, place)
Web chat (/api/chat)┘   gpt-oss-120b,     ├─ tools/complaints.py (photo check → vision model)
                        tool loop ≤6)     └─ tools/fraud.py      (hash, timing, history, EXIF)
                              │
                           db.py  ── Supabase if SUPABASE_URL else SQLite (data/swish.db)
                              │
 simulator.py (background task moves live orders)   events bus → SSE → dashboard.html
```

Key decisions
- `db.py` exposes a tiny table-level interface (`insert`, `select`, `update`, `delete`, `count`) so the same tool code runs on SQLite or Supabase. JSON columns are stored as TEXT in SQLite and JSONB in Supabase; the layer (de)serialises transparently.
- `llm.py` is the only file that imports `groq`. Two functions: `chat_with_tools()` and `vision_json()`. Retries (3, exponential) on 429/5xx, model fallback on model-not-found/decommissioned, Pillow resize to ≤1280px JPEG q85 (step down until <3.5 MB).
- The chat model never sees images: uploaded images get an `image_id` and the user turn says "user attached image img_x".
- Decision rules are a pure function `decide()` in complaints.py, unit-tested branch by branch.
- **Offline demo mode**: if `GROQ_API_KEY` is missing, the agent uses a small rule-based router and the vision step uses a deterministic mock, so the whole demo still clicks through. Tests mock Groq explicitly.
- Dashboard updates via an in-process pub/sub → SSE (`/api/events`), 3-second polling fallback.

## Deviations found while building
- `qwen/qwen3.6-27b` and `meta-llama/llama-4-scout-17b-16e-instruct` return `model_not_found` on the tested Groq key; `qwen/qwen3.8-27b` is available and works with images + JSON mode, so it is the default. The fallback stays configurable.
- Groq free tier: 8k TPM on gpt-oss-120b, 7k ITPM on the vision model. On a 429 the chat call switches straight to gpt-oss-20b (own bucket) and retries honour `retry-after`.
- Only items the customer names can be auto-refunded; other items the photo can't see are noted for the human.
- Cheap fraud checks (hash, timing, refund history, EXIF) run before the vision call; any flag escalates without calling the vision model ("Vision calls saved" tile).
- Seeded demo orders are kept "fresh" by the simulator so the demo works hours after a deploy.

## Phases & checks
1. Setup, config, db (SQLite + Supabase), schema, seed (`--reset`, idempotent). ✔ seed twice → same counts.
2. Order tools, coupon engine, usual-order matcher, simulator + tests. ✔ pytest.
3. llm.py + agent loop + CLI chat (`python -m app.agent --user priya`). ✔ CLI flows (offline mode without key).
4. Photo engine: resize, vision, fraud, decision + tests. ✔ pytest + sample image.
5. FastAPI routes, chat.html, dashboard.html, /demo split. ✔ run server, hit all 3 jobs via HTTP.
6. WhatsApp webhook + test.
7. Eval script, landing, README, render.yaml, final test run.
