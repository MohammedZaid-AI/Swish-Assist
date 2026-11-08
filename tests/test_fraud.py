import io
from datetime import datetime, timedelta, timezone

from PIL import Image

from app.models import IST, VisionResult
from app.tools import fraud

NOW = datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc)


def test_duplicate_near_match():
    past = [{"id": "c1", "image_hash": "eaa5953a6c429f85", "user_id": "sneha", "order_id": "SW1"}]
    f = fraud.check_duplicate("eaa5953a6c429f84", past)  # 1 bit different
    assert f and f["code"] == "duplicate_photo" and "c1" in f["detail"]


def test_duplicate_far_hash_is_fine():
    past = [{"id": "c1", "image_hash": "0000000000000000", "user_id": "x", "order_id": "SW1"}]
    assert fraud.check_duplicate("ffffffffffffffff", past) is None
    assert fraud.check_duplicate("ffffffffffffffff", [{"id": "c2", "image_hash": None}]) is None


def test_hamming_handles_garbage():
    assert fraud.hamming("zz", "00") == 64


def test_timing_ok_within_3_hours():
    assert fraud.check_timing(NOW, "delivered", NOW - timedelta(hours=2, minutes=59)) is None


def test_timing_late():
    f = fraud.check_timing(NOW, "delivered", NOW - timedelta(hours=3, minutes=5))
    assert f["code"] == "late_complaint"


def test_timing_before_delivery():
    assert fraud.check_timing(NOW, "out_for_delivery", None)["code"] == "before_delivery"


def test_refund_rate():
    ids = [f"o{i}" for i in range(10)]
    complaints = [{"order_id": "o1", "decision": "auto_refund"}, {"order_id": "o2", "decision": "escalated", "human_decision": "approved"},
                  {"order_id": "o3", "decision": "escalated", "human_decision": "rejected"}, {"order_id": "o4", "decision": "no_issue_found"},
                  {"order_id": "zz", "decision": "auto_refund"}]
    rate, n = fraud.refund_rate(ids, complaints)
    assert n == 2 and rate == 0.2
    assert fraud.check_refund_history(rate, n, 10) is None
    assert fraud.check_refund_history(0.4, 4, 10)["code"] == "high_refund_rate"
    assert fraud.refund_rate([], complaints) == (0.0, 0)


def test_photo_content_not_food():
    v = VisionResult(is_food_photo=False, overall_confidence=0.9)
    assert fraud.check_photo_content(v, ["Fries"])["code"] == "not_food"


def test_photo_content_mismatch():
    v = VisionResult.model_validate({"items_missing": [{"name": "Classic Fries", "confidence": 0.9}, {"name": "Cold Coffee", "confidence": 0.9}],
                                     "overall_confidence": 0.9})
    assert fraud.check_photo_content(v, ["Classic Fries", "Cold Coffee"])["code"] == "photo_mismatch"


def test_photo_content_partial_is_fine():
    v = VisionResult.model_validate({"items_seen": [{"name": "Cold Coffee", "confidence": 0.9}],
                                     "items_missing": [{"name": "Classic Fries", "confidence": 0.9}], "overall_confidence": 0.9})
    assert fraud.check_photo_content(v, ["Classic Fries", "Cold Coffee"]) is None


def _jpeg_with_exif(taken: datetime) -> bytes:
    img = Image.new("RGB", (64, 64), "orange")
    exif = Image.Exif()
    exif[306] = taken.strftime("%Y:%m:%d %H:%M:%S")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_exif_old_photo_flagged():
    placed = NOW
    taken = (NOW - timedelta(days=2)).astimezone(IST).replace(tzinfo=None)
    data = _jpeg_with_exif(taken)
    dt = fraud.exif_datetime(data)
    assert dt is not None
    assert fraud.check_exif(dt, placed)["code"] == "old_photo"


def test_exif_fresh_photo_ok_and_missing_exif_ok():
    fresh = (NOW + timedelta(minutes=12)).astimezone(IST).replace(tzinfo=None)
    assert fraud.check_exif(fraud.exif_datetime(_jpeg_with_exif(fresh)), NOW) is None
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    assert fraud.exif_datetime(buf.getvalue()) is None
    assert fraud.check_exif(None, NOW) is None


def test_phash_same_image_same_hash():
    data = open("static/samples/burger_no_fries.jpg", "rb").read()
    assert fraud.phash(data) == fraud.phash(data)
