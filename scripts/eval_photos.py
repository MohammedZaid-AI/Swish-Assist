"""Measure the photo checker on labeled photos.

    python scripts/eval_photos.py                    # default vision model
    python scripts/eval_photos.py --model meta-llama/llama-4-scout-17b-16e-instruct
    python scripts/eval_photos.py --compare          # primary vs fallback vision model

Reads test_images/labels.json, runs the full check (vision + decision rules, no DB writes and no
fraud checks) on every case, prints per-case results, precision / recall for missing-item detection,
average latency and the decision distribution, and saves eval_results.json.

Scoring: an ordered item counts as "flagged" if the model reports it missing or wrong. A case's
expected problems are expected_missing + expected_wrong.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROOT, settings  # noqa: E402
from app.tools.complaints import match_item, run_photo_check  # noqa: E402

IMAGES = ROOT / "test_images"
LABELS = IMAGES / "labels.json"
OUT = ROOT / "eval_results.json"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

EXAMPLE = {
    "about": "Each case pairs a photo with an order. expected_missing = ordered items NOT visible in the photo.",
    "cases": [
        {"image": "burger.jpg", "order_items": [{"name": "Grilled Chicken Cheese Burger", "qty": 1, "price": 179},
                                                {"name": "Classic Fries", "qty": 1, "price": 99}], "expected_missing": ["Classic Fries"]},
        {"image": "momos.jpg", "order_items": [{"name": "Veg Momos (6 pcs)", "qty": 1, "price": 109}], "expected_missing": []},
    ],
}

INSTRUCTIONS = """
No labeled photos found in test_images/.

How to add 10 to 20 test photos:
  1. Put food photos in test_images/ (jpg or png). Photos of real deliveries work best:
     a bag with some items missing, a plate with everything, a wrong dish, a non-food photo.
  2. For each photo, add a case to test_images/labels.json:
       {"image": "burger.jpg",
        "order_items": [{"name": "Classic Fries", "qty": 1, "price": 99}, ...],
        "expected_missing": ["Classic Fries"],
        "expected_wrong": []}
     Reuse one photo with different orders to get more cases.
  3. Run: python scripts/eval_photos.py
An example labels.json has been written for you.
"""


def _order(case: dict) -> dict:
    items = [{"menu_item_id": None, "name": i["name"], "qty": i.get("qty", 1), "price": i.get("price", 100)} for i in case["order_items"]]
    return {"id": "EVAL", "items": items, "total": sum(i["price"] * i["qty"] for i in items), "status": "delivered",
            "kitchen_distance_km": 0.8, "placed_at": None, "delivered_at": None}


def run(model: str | None, cases: list[dict], delay: float) -> dict:
    results, tp, fp, fn = [], 0, 0, 0
    latencies, decisions = [], Counter()
    for n, case in enumerate(cases, start=1):
        path = IMAGES / case["image"]
        order = _order(case)
        expected = {match_item(x, order["items"])["name"] for x in case.get("expected_missing", []) + case.get("expected_wrong", [])
                    if match_item(x, order["items"])}
        t = time.perf_counter()
        res = run_photo_check(user_id="eval", order=order, image=path.read_bytes(), user_message="something is missing or wrong",
                              persist=False, model=model)
        vision = res.get("vision")
        flagged = set()
        if vision:
            for m in vision.get("items_missing", []) + vision.get("wrong_items", []):
                it = match_item(m.get("name") or m.get("expected"), order["items"])
                if it and (m.get("confidence") or 0) >= 0.5:
                    flagged.add(it["name"])
        c_tp, c_fp, c_fn = len(flagged & expected), len(flagged - expected), len(expected - flagged)
        tp, fp, fn = tp + c_tp, fp + c_fp, fn + c_fn
        if res["vision_latency_ms"]:
            latencies.append(res["vision_latency_ms"])
        decisions[res["decision"]] += 1
        ok = "✓" if flagged == expected else "✗"
        print(f"{ok} {n:>2}. {case['image']:<16} expected {sorted(expected) or '-'}  got {sorted(flagged) or '-'}  "
              f"-> {res['decision']} ({res['vision_latency_ms']} ms, {res['vision_model']})" + ("" if vision else "  [vision failed]"))
        results.append({"image": case["image"], "expected": sorted(expected), "flagged": sorted(flagged), "correct": flagged == expected,
                        "decision": res["decision"], "latency_ms": res["vision_latency_ms"], "model": res["vision_model"],
                        "confidence": (vision or {}).get("overall_confidence"), "reason": (vision or {}).get("short_reason"),
                        "wall_ms": int((time.perf_counter() - t) * 1000)})
        if delay and n < len(cases):
            time.sleep(delay)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    used = Counter(r["model"] for r in results).most_common(1)[0][0] if results else model
    summary = {"model": used, "cases": len(cases), "exact_match": sum(r["correct"] for r in results),
               "precision": precision, "recall": recall, "true_pos": tp, "false_pos": fp, "false_neg": fn,
               "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
               "median_latency_ms": statistics.median(latencies) if latencies else None,
               "vision_failures": sum(1 for r in results if not r["latency_ms"]), "decisions": dict(decisions)}
    return {"summary": summary, "cases": results}


def _fmt(x, pct=True):
    if x is None:
        return "–"
    return f"{x:.0%}" if pct else f"{x:.0f}"


def print_summary(s: dict) -> None:
    print(f"\nModel: {s['model']}   cases: {s['cases']}   exact match: {s['exact_match']}/{s['cases']}")
    print(f"Missing/wrong item detection   precision {_fmt(s['precision'])}   recall {_fmt(s['recall'])}"
          f"   (TP {s['true_pos']}, FP {s['false_pos']}, FN {s['false_neg']})")
    print(f"Vision latency: median {_fmt(s['median_latency_ms'], False)} ms, average {_fmt(s['avg_latency_ms'], False)} ms (includes rate-limit waits)   vision failures: {s['vision_failures']}")
    print("Decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(s["decisions"].items())))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="vision model to use (default: GROQ_VISION_MODEL)")
    ap.add_argument("--compare", action="store_true", help="run GROQ_VISION_MODEL and GROQ_VISION_FALLBACK_MODEL and compare")
    ap.add_argument("--delay", type=float, default=None, help="seconds between cases (default 20 with Groq free tier limits, 0 offline)")
    ap.add_argument("--limit", type=int, help="only run the first N cases")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    images = [p for p in IMAGES.glob("*") if p.suffix.lower() in IMAGE_EXT] if IMAGES.exists() else []
    if not images or not LABELS.exists():
        IMAGES.mkdir(exist_ok=True)
        if not LABELS.exists():
            LABELS.write_text(json.dumps(EXAMPLE, indent=2), encoding="utf-8")
        print(INSTRUCTIONS)
        return
    cases = json.loads(LABELS.read_text(encoding="utf-8"))["cases"]
    missing = [c["image"] for c in cases if not (IMAGES / c["image"]).exists()]
    if missing:
        print(f"Skipping cases with missing photos: {', '.join(sorted(set(missing)))}")
    cases = [c for c in cases if (IMAGES / c["image"]).exists()][: args.limit]
    if not cases:
        print(INSTRUCTIONS)
        return

    offline = not settings.llm_enabled
    if offline:
        print("GROQ_API_KEY is not set: running the OFFLINE MOCK. These numbers are NOT real accuracy.\n")
    delay = args.delay if args.delay is not None else (0 if offline else 20)
    if delay:
        print(f"Waiting {delay:.0f}s between cases to stay inside Groq free-tier token limits (use --delay 0 on a paid tier).\n")

    models = [args.model or settings.GROQ_VISION_MODEL]
    if args.compare:
        models = [settings.GROQ_VISION_MODEL, settings.GROQ_VISION_FALLBACK_MODEL]
    runs = {}
    for m in models:
        print(f"=== {m} ===")
        runs[m] = run(m, cases, delay)
        print_summary(runs[m]["summary"])
        print()

    if len(runs) > 1:
        print(f"{'model':<46} {'precision':>9} {'recall':>7} {'exact':>6} {'latency':>9} {'failures':>9}")
        for m, r in runs.items():
            s = r["summary"]
            print(f"{m:<46} {_fmt(s['precision']):>9} {_fmt(s['recall']):>7} {s['exact_match']:>3}/{s['cases']:<2} "
                  f"{_fmt(s['avg_latency_ms'], False):>6} ms {s['vision_failures']:>9}")

    primary = runs[models[0]]
    out = {"mode": "offline-mock" if offline else "groq", "summary": primary["summary"], "cases": primary["cases"],
           "comparison": {m: r["summary"] for m, r in runs.items()} if len(runs) > 1 else None,
           "ran_at": time.strftime("%Y-%m-%d %H:%M")}
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {OUT.name}")


if __name__ == "__main__":
    main()
