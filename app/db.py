"""One small table-level interface over Supabase Postgres or a local SQLite file.

If SUPABASE_URL and SUPABASE_KEY are set, Supabase is used. Otherwise a SQLite
file at SQLITE_PATH is used, so the demo runs with zero setup.

Tool code only ever calls: insert, select, get, update, delete, count.
JSON columns are (de)serialised here, booleans are normalised here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger("swish.db")

# column -> type. Types: text, int, real, bool, json, ts. "pk" marks the primary key.
SCHEMA: dict[str, dict[str, str]] = {
    "menu_items": {
        "id": "text pk", "name": "text", "category": "text", "price_inr": "int",
        "is_veg": "bool", "description": "text", "image_url": "text", "typical_look": "text",
    },
    "users": {
        "id": "text pk", "phone": "text", "name": "text", "language_pref": "text", "created_at": "ts",
    },
    "orders": {
        "id": "text pk", "user_id": "text", "items": "json", "subtotal": "int", "discount": "int",
        "total": "int", "coupon_code": "text", "status": "text", "placed_at": "ts",
        "preparing_at": "ts", "packed_at": "ts", "out_for_delivery_at": "ts", "delivered_at": "ts",
        "eta_minutes": "int", "delay_reason": "text", "kitchen_name": "text",
        "kitchen_distance_km": "real", "rider_name": "text",
    },
    "coupons": {
        "code": "text pk", "description": "text", "type": "text", "value": "int",
        "min_order_inr": "int", "max_discount_inr": "int", "valid_from": "ts", "valid_to": "ts",
        "first_order_only": "bool", "max_uses_per_user": "int", "allowed_days": "json",
        "allowed_hours": "json", "active": "bool",
    },
    "coupon_uses": {
        "id": "text pk", "user_id": "text", "coupon_code": "text", "order_id": "text", "used_at": "ts",
    },
    "complaints": {
        "id": "text pk", "order_id": "text", "user_id": "text", "type": "text", "user_message": "text",
        "image_url": "text", "image_hash": "text", "vision_model": "text", "vision_latency_ms": "int",
        "vision_result": "json", "fraud_flags": "json", "decision": "text", "refund_amount_inr": "int",
        "decision_reason": "text", "human_decision": "text", "created_at": "ts", "resolved_at": "ts",
        "resolution_seconds": "real",
    },
    "conversations": {
        "id": "autoid pk", "user_id": "text", "channel": "text", "role": "text", "content": "text",
        "tool_name": "text", "tool_payload": "json", "created_at": "ts",
    },
}

PK = {t: next(c for c, ty in cols.items() if "pk" in ty) for t, cols in SCHEMA.items()}

_SQLITE_TYPES = {"text": "TEXT", "int": "INTEGER", "real": "REAL", "bool": "INTEGER", "json": "TEXT", "ts": "TEXT"}


def _coltype(table: str, col: str) -> str:
    return SCHEMA[table].get(col, "text").split()[0]


class Database:
    """Base class: conversion helpers shared by both backends."""

    backend = "base"

    def _from_row(self, table: str, row: dict) -> dict:
        out = {}
        for k, v in row.items():
            t = _coltype(table, k)
            if t == "json" and isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            elif t == "bool" and v is not None:
                v = bool(v)
            out[k] = v
        return out

    # interface -------------------------------------------------------------
    def insert(self, table: str, row: dict) -> dict: raise NotImplementedError
    def select(self, table: str, where: dict | None = None, order_by: str | None = None,
               desc: bool = False, limit: int | None = None) -> list[dict]: raise NotImplementedError
    def update(self, table: str, where: dict, values: dict) -> None: raise NotImplementedError
    def delete(self, table: str, where: dict | None = None) -> None: raise NotImplementedError

    def get(self, table: str, **where: Any) -> dict | None:
        rows = self.select(table, where, limit=1)
        return rows[0] if rows else None

    def count(self, table: str, where: dict | None = None) -> int:
        return len(self.select(table, where))

    def create_tables(self) -> None:
        pass


class SQLiteDatabase(Database):
    backend = "sqlite"

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self.lock = threading.RLock()
        self.create_tables()

    def create_tables(self) -> None:
        with self.lock:
            for table, cols in SCHEMA.items():
                defs = []
                for c, ty in cols.items():
                    if ty.startswith("autoid"):
                        defs.append(f"{c} INTEGER PRIMARY KEY AUTOINCREMENT")
                    else:
                        base = _SQLITE_TYPES[ty.split()[0]]
                        defs.append(f"{c} {base}{' PRIMARY KEY' if 'pk' in ty else ''}")
                self.conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(defs)})")
            self.conn.commit()

    def _to_db(self, table: str, row: dict) -> dict:
        out = {}
        for k, v in row.items():
            if k not in SCHEMA[table]:
                continue
            t = _coltype(table, k)
            if t == "json" and v is not None:
                v = json.dumps(v)
            elif t == "bool" and v is not None:
                v = int(bool(v))
            out[k] = v
        return out

    @staticmethod
    def _where(where: dict | None) -> tuple[str, list]:
        if not where:
            return "", []
        parts, args = [], []
        for k, v in where.items():
            if v is None:
                parts.append(f"{k} IS NULL")
            elif isinstance(v, (list, tuple, set)):
                v = list(v)
                parts.append(f"{k} IN ({','.join('?' * len(v))})" if v else "0")
                args.extend(v)
            else:
                parts.append(f"{k} = ?")
                args.append(int(v) if isinstance(v, bool) else v)
        return " WHERE " + " AND ".join(parts), args

    def insert(self, table: str, row: dict) -> dict:
        data = self._to_db(table, row)
        cols = ", ".join(data)
        q = f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(data))})"
        with self.lock:
            cur = self.conn.execute(q, list(data.values()))
            self.conn.commit()
        out = dict(row)
        if SCHEMA[table][PK[table]].startswith("autoid") and PK[table] not in out:
            out[PK[table]] = cur.lastrowid
        return out

    def select(self, table, where=None, order_by=None, desc=False, limit=None):
        w, args = self._where(where)
        q = f"SELECT * FROM {table}{w}"
        if order_by:
            q += f" ORDER BY {order_by} {'DESC' if desc else 'ASC'}"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
        return [self._from_row(table, dict(r)) for r in rows]

    def update(self, table, where, values):
        data = self._to_db(table, values)
        if not data:
            return
        w, args = self._where(where)
        q = f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in data)}{w}"
        with self.lock:
            self.conn.execute(q, list(data.values()) + args)
            self.conn.commit()

    def delete(self, table, where=None):
        w, args = self._where(where)
        with self.lock:
            self.conn.execute(f"DELETE FROM {table}{w}", args)
            self.conn.commit()

    def count(self, table, where=None):
        w, args = self._where(where)
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}{w}", args).fetchone()[0]


class SupabaseDatabase(Database):
    """Supabase backend. Tables must exist: run sql/schema.sql in the Supabase SQL editor once."""

    backend = "supabase"

    def __init__(self, url: str, key: str) -> None:
        from supabase import create_client

        self.client = create_client(url, key)

    def _q(self, table: str, query, where: dict | None):
        for k, v in (where or {}).items():
            if v is None:
                query = query.is_(k, "null")
            elif isinstance(v, (list, tuple, set)):
                query = query.in_(k, list(v))
            else:
                query = query.eq(k, v)
        return query

    def _clean(self, table: str, row: dict) -> dict:
        return {k: v for k, v in row.items() if k in SCHEMA[table]}

    def insert(self, table, row):
        res = self.client.table(table).insert(self._clean(table, row)).execute()
        return self._from_row(table, res.data[0]) if res.data else row

    def select(self, table, where=None, order_by=None, desc=False, limit=None):
        q = self._q(table, self.client.table(table).select("*"), where)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit:
            q = q.limit(limit)
        return [self._from_row(table, r) for r in q.execute().data]

    def update(self, table, where, values):
        self._q(table, self.client.table(table).update(self._clean(table, values)), where).execute()

    def delete(self, table, where=None):
        q = self.client.table(table).delete()
        if where:
            q = self._q(table, q, where)
        else:  # PostgREST refuses unfiltered deletes
            q = q.neq(PK[table], -1 if SCHEMA[table][PK[table]].startswith("autoid") else "__none__")
        q.execute()

    def count(self, table, where=None):
        q = self._q(table, self.client.table(table).select(PK[table], count="exact"), where)
        return q.execute().count or 0


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            if settings.use_supabase:
                log.info("Using Supabase at %s", settings.SUPABASE_URL)
                _db = SupabaseDatabase(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            else:
                log.info("Using SQLite at %s", settings.SQLITE_PATH)
                _db = SQLiteDatabase(settings.SQLITE_PATH)
        return _db


def set_db(db: Database | None) -> None:
    """Used by tests to swap in an in-memory database."""
    global _db
    with _db_lock:
        _db = db
