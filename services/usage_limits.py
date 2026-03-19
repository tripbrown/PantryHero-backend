import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo

from supabase import create_client

RATE_LIMIT_SECONDS = 10
DEFAULT_WEEKLY_LIMIT = int(os.getenv("PANTRYHERO_WEEKLY_LIMIT", "10"))
PLAN = os.getenv("PANTRYHERO_PLAN", "free")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
USAGE_TABLE = "usage_limits"

TZ = ZoneInfo("America/New_York")


@dataclass
class LimitResult:
    allowed: bool
    error: Dict[str, Any]
    quota: Dict[str, Any]
    debug: Dict[str, Any]
    reason: str


def _get_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _fetch_record(user_key: str) -> Dict[str, Any]:
    client = _get_client()
    response = client.table(USAGE_TABLE).select("*").eq("user_key", user_key).execute()
    data = response.data or []
    if data:
        return data[0]
    return {}


def _upsert_record(record: Dict[str, Any]) -> None:
    client = _get_client()
    client.table(USAGE_TABLE).upsert(record, on_conflict="user_key").execute()


def _next_window_end(now_ts: int) -> int:
    now = datetime.fromtimestamp(now_ts, TZ)
    days_ahead = (6 - now.weekday()) % 7
    next_sunday = now + timedelta(days=days_ahead)
    window_end = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
    if window_end <= now:
        window_end = window_end + timedelta(days=7)
    return int(window_end.timestamp())


def enforce_limits(user_key: str) -> LimitResult:
    now = time.time()

    if PLAN == "unlimited":
        return LimitResult(
            True,
            {},
            {"limit": DEFAULT_WEEKLY_LIMIT, "remaining": DEFAULT_WEEKLY_LIMIT, "reset_in_seconds": 0},
            {"now": now, "last_attempt": 0.0, "delta": 0.0, "retry_after": 0},
            "ok",
        )

    record = _fetch_record(user_key)

    last_attempt_ts = float(record.get("last_attempt_ts", 0) or 0)
    delta = now - last_attempt_ts
    if delta < RATE_LIMIT_SECONDS:
        retry_after = int((RATE_LIMIT_SECONDS - delta) + 0.999)
        return LimitResult(
            False,
            {"error": "rate_limited", "retry_after_seconds": retry_after},
            {},
            {"now": now, "last_attempt": last_attempt_ts, "delta": delta, "retry_after": retry_after},
            "rate_limited",
        )

    record["last_attempt_ts"] = now
    record["user_key"] = user_key
    record["updated_at"] = datetime.utcnow().isoformat()

    window_end_ts = int(record.get("window_end_ts", 0) or 0)
    if window_end_ts == 0 or now >= window_end_ts:
        window_end_ts = _next_window_end(int(now))
        record["count_used"] = 0
        record["window_end_ts"] = window_end_ts

    count_used = int(record.get("count_used", 0) or 0)
    remaining = max(0, DEFAULT_WEEKLY_LIMIT - count_used)
    reset_in = max(0, window_end_ts - int(now))
    if remaining == 0:
        _upsert_record(record)
        return LimitResult(
            False,
            {"error": "quota_exceeded", "weekly_limit": DEFAULT_WEEKLY_LIMIT, "remaining": 0, "reset_in_seconds": reset_in},
            {},
            {"now": now, "last_attempt": last_attempt_ts, "delta": delta, "retry_after": 0},
            "quota_exceeded",
        )

    _upsert_record(record)
    return LimitResult(
        True,
        {},
        {"limit": DEFAULT_WEEKLY_LIMIT, "remaining": remaining, "reset_in_seconds": reset_in},
        {"now": now, "last_attempt": now, "delta": delta, "retry_after": 0},
        "ok",
    )


def record_success(user_key: str) -> Dict[str, Any]:
    now = int(time.time())
    record = _fetch_record(user_key)

    window_end_ts = int(record.get("window_end_ts", 0) or 0)
    if window_end_ts == 0 or now >= window_end_ts:
        window_end_ts = _next_window_end(now)
        record["count_used"] = 0
        record["window_end_ts"] = window_end_ts

    record["count_used"] = int(record.get("count_used", 0) or 0) + 1
    record["last_generation_ts"] = now
    record["user_key"] = user_key
    record["updated_at"] = datetime.utcnow().isoformat()
    _upsert_record(record)

    remaining = max(0, DEFAULT_WEEKLY_LIMIT - record["count_used"])
    reset_in = max(0, record["window_end_ts"] - now)
    return {"limit": DEFAULT_WEEKLY_LIMIT, "remaining": remaining, "reset_in_seconds": reset_in}


def log_rate_event(user_key: str, now: float, last_attempt: float, delta: float, allowed: bool, retry_after: int) -> None:
    logging.info(
        "RATE key=%s now=%.2f last_attempt=%.2f delta=%.2f allowed=%s retry_after=%s",
        user_key[:12],
        now,
        last_attempt,
        delta,
        allowed,
        retry_after,
    )


def log_decision(user_key: str, allowed: bool, reason: str, remaining: int, reset_in: int) -> None:
    truncated = user_key[:8]
    logging.info(
        "quota user=%s allowed=%s reason=%s remaining=%s reset_in=%s",
        truncated,
        allowed,
        reason,
        remaining,
        reset_in,
    )
