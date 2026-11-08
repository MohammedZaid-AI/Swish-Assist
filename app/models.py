"""Pydantic models and small shared helpers (time handling)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

IST = timezone(timedelta(hours=5, minutes=30))

OrderStatus = Literal["placed", "preparing", "packed", "out_for_delivery", "delivered", "cancelled"]
STAGES: list[str] = ["placed", "preparing", "packed", "out_for_delivery", "delivered"]
LIVE_STATUSES = ["placed", "preparing", "packed", "out_for_delivery"]
Decision = Literal["auto_refund", "auto_replace", "escalated", "rejected", "no_issue_found"]


# ---------------------------------------------------------------- time helpers
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST)


# ---------------------------------------------------------------- domain models
class CartLine(BaseModel):
    menu_item_id: str
    name: str = ""
    qty: int = 1
    price: int = 0


class OrderItem(CartLine):
    pass


# ---------------------------------------------------------------- vision result
def _clamp(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0 and v <= 100.0:  # some models answer in percent
        v = v / 100.0
    return max(0.0, min(1.0, v))


class SeenItem(BaseModel):
    name: str
    qty_estimate: int = 1
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return _clamp(v)


class MissingItem(BaseModel):
    name: str
    qty_missing: int = 1
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return _clamp(v)


class WrongItem(BaseModel):
    expected: str
    seen: str = ""
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return _clamp(v)


class QualityIssue(BaseModel):
    present: bool = False
    description: str = ""


class VisionResult(BaseModel):
    items_seen: list[SeenItem] = Field(default_factory=list)
    items_missing: list[MissingItem] = Field(default_factory=list)
    wrong_items: list[WrongItem] = Field(default_factory=list)
    quality_issue: QualityIssue = Field(default_factory=QualityIssue)
    is_food_photo: bool = True
    overall_confidence: float = 0.0
    short_reason: str = ""

    @field_validator("overall_confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return _clamp(v)

    @field_validator("quality_issue", mode="before")
    @classmethod
    def _qi(cls, v):
        if v is None:
            return {"present": False, "description": ""}
        if isinstance(v, bool):
            return {"present": v, "description": ""}
        return v


class DecisionResult(BaseModel):
    decision: Decision
    refund_amount_inr: int = 0
    reason: str
