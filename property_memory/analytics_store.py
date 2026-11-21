"""Private, privacy-limited Website event storage, separate from Property Core."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

EVENT_TYPES = frozenset({"page_view", "property_card_view", "property_detail_view", "search_filter", "contact_cta_click"})
PAYLOAD_KEYS = frozenset({"event_id", "event_type", "property_id", "page_type", "channel", "filter_type", "filter_value", "referrer_host", "utm_source", "utm_medium", "utm_campaign"})
FILTER_VALUES = {
    "transaction": frozenset({"sale", "rent"}),
    "property_type": frozenset({"apartment", "house", "land"}),
    "price_band": frozenset({"under_1b", "1_2b", "2_3b", "3_5b", "5_10b", "over_10b"}),
    "location": frozenset({"thon-8", "ecocity", "ea-tu", "ea-kao", "tan-an", "trung-tam"}),
    "query_used": frozenset({"yes"}),
}
CHANNELS = frozenset({"call", "zalo", "other"})
UTM_SOURCES = frozenset({"google", "facebook", "zalo", "direct", "qr", "other"})
UTM_MEDIA = frozenset({"organic", "paid", "social", "referral", "email", "qr", "other"})
_HOST_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*\Z")
_CAMPAIGN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}\Z")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LOCK = threading.RLock()


class AnalyticsValidationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_KEYS:
        raise AnalyticsValidationError("ANALYTICS_PAYLOAD_KEYS_REJECTED")
    try:
        event_id = str(uuid.UUID(str(payload.get("event_id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AnalyticsValidationError("ANALYTICS_EVENT_ID_INVALID") from exc
    if uuid.UUID(event_id).version != 4:
        raise AnalyticsValidationError("ANALYTICS_EVENT_ID_INVALID")
    event_type = payload.get("event_type")
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise AnalyticsValidationError("ANALYTICS_EVENT_NOT_ALLOWED")

    property_id = payload.get("property_id")
    if property_id is not None and (not isinstance(property_id, str) or not re.fullmatch(r"property-[a-f0-9]{8,64}", property_id)):
        raise AnalyticsValidationError("ANALYTICS_PROPERTY_ID_INVALID")

    page_type = payload.get("page_type")
    channel = payload.get("channel")
    filter_type = payload.get("filter_type")
    filter_value = payload.get("filter_value")
    if event_type == "page_view":
        if not isinstance(page_type, str) or page_type not in {"home", "listing_detail", "other"}:
            raise AnalyticsValidationError("ANALYTICS_PAGE_TYPE_INVALID")
    elif page_type is not None:
        raise AnalyticsValidationError("ANALYTICS_FIELD_NOT_APPLICABLE")
    if event_type == "contact_cta_click":
        if not isinstance(channel, str) or channel not in CHANNELS:
            raise AnalyticsValidationError("ANALYTICS_CHANNEL_INVALID")
    elif channel is not None:
        raise AnalyticsValidationError("ANALYTICS_FIELD_NOT_APPLICABLE")
    if event_type == "search_filter":
        if (not isinstance(filter_type, str) or filter_type not in FILTER_VALUES
                or not isinstance(filter_value, str) or filter_value not in FILTER_VALUES[filter_type]):
            raise AnalyticsValidationError("ANALYTICS_FILTER_VALUE_INVALID")
    elif filter_type is not None or filter_value is not None:
        raise AnalyticsValidationError("ANALYTICS_FIELD_NOT_APPLICABLE")

    referrer_host = payload.get("referrer_host")
    if referrer_host is not None:
        if not isinstance(referrer_host, str) or not _HOST_RE.fullmatch(referrer_host.lower()) or re.fullmatch(r"\d+(?:\.\d+){3}", referrer_host):
            raise AnalyticsValidationError("ANALYTICS_REFERRER_INVALID")
        referrer_host = referrer_host.lower()
    utm_source = payload.get("utm_source")
    utm_medium = payload.get("utm_medium")
    utm_campaign = payload.get("utm_campaign")
    if utm_source is not None and (not isinstance(utm_source, str) or utm_source not in UTM_SOURCES):
        raise AnalyticsValidationError("ANALYTICS_UTM_SOURCE_INVALID")
    if utm_medium is not None and (not isinstance(utm_medium, str) or utm_medium not in UTM_MEDIA):
        raise AnalyticsValidationError("ANALYTICS_UTM_MEDIUM_INVALID")
    if utm_campaign is not None:
        if (not isinstance(utm_campaign, str) or not _CAMPAIGN_RE.fullmatch(utm_campaign)
                or _PHONE_RE.search(utm_campaign) or _EMAIL_RE.search(utm_campaign)):
            raise AnalyticsValidationError("ANALYTICS_UTM_CAMPAIGN_INVALID")

    return {
        "event_id": event_id, "event_type": event_type, "property_id": property_id,
        "page_type": page_type, "channel": channel, "filter_type": filter_type,
        "filter_value": filter_value, "referrer_host": referrer_host,
        "utm_source": utm_source, "utm_medium": utm_medium, "utm_campaign": utm_campaign,
    }


class AnalyticsStore:
    def __init__(self, path: str | Path, *, retention_days: int = 90, max_bytes: int = 4096):
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
            raise ValueError("ANALYTICS_RETENTION_CONFIG_INVALID")
        self.path = Path(path)
        self.retention_days = retention_days
        self.max_bytes = max_bytes

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise OSError("ANALYTICS_STORAGE_NOT_PRIVATE")
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("""CREATE TABLE IF NOT EXISTS analytics_events (
            event_id TEXT PRIMARY KEY,
            event_hash TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            property_id TEXT,
            page_type TEXT,
            channel TEXT,
            filter_type TEXT,
            filter_value TEXT,
            referrer_host TEXT,
            utm_source TEXT,
            utm_medium TEXT,
            utm_campaign TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_date_property ON analytics_events(created_at, property_id)")
        if self.path.exists():
            self.path.chmod(0o600)
        return conn

    def _purge(self, conn: sqlite3.Connection, now: datetime) -> int:
        cutoff = _iso(now - timedelta(days=self.retention_days))
        cursor = conn.execute("DELETE FROM analytics_events WHERE created_at < ?", (cutoff,))
        return cursor.rowcount

    def ingest(self, payload: Any, *, property_is_public: Callable[[str], bool] | None = None) -> dict[str, Any]:
        normalized = normalize_event(payload)
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise AnalyticsValidationError("ANALYTICS_PAYLOAD_TOO_LARGE")
        property_id = normalized["property_id"]
        if property_id and (property_is_public is None or not property_is_public(property_id)):
            raise AnalyticsValidationError("ANALYTICS_PROPERTY_NOT_PUBLIC")
        event_hash = hashlib.sha256(encoded).hexdigest()
        now = _now()
        with _LOCK:
            conn = self._connect()
            try:
                self._purge(conn, now)
                existing = conn.execute("SELECT event_hash FROM analytics_events WHERE event_id=?", (normalized["event_id"],)).fetchone()
                if existing:
                    if existing["event_hash"] != event_hash:
                        conn.rollback()
                        raise AnalyticsValidationError("ANALYTICS_EVENT_ID_CONFLICT")
                    conn.commit()
                    return {"accepted": True, "duplicate": True}
                conn.execute("""INSERT INTO analytics_events
                    (event_id,event_hash,event_type,created_at,property_id,page_type,channel,filter_type,filter_value,referrer_host,utm_source,utm_medium,utm_campaign)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (normalized["event_id"], event_hash, normalized["event_type"], _iso(now), property_id,
                     normalized["page_type"], normalized["channel"], normalized["filter_type"], normalized["filter_value"],
                     normalized["referrer_host"], normalized["utm_source"], normalized["utm_medium"], normalized["utm_campaign"]))
                conn.commit()
                self.path.chmod(0o600)
                return {"accepted": True, "duplicate": False}
            finally:
                conn.close()

    def summary(self, *, start_date: date, end_date: date, property_id: str | None = None,
                event_type: str | None = None, channel: str | None = None) -> dict[str, Any]:
        if end_date < start_date or (end_date - start_date).days >= self.retention_days:
            raise AnalyticsValidationError("ANALYTICS_DATE_RANGE_INVALID")
        if property_id is not None and not re.fullmatch(r"property-[a-f0-9]{8,64}", property_id):
            raise AnalyticsValidationError("ANALYTICS_PROPERTY_ID_INVALID")
        if event_type is not None and event_type not in EVENT_TYPES:
            raise AnalyticsValidationError("ANALYTICS_EVENT_NOT_ALLOWED")
        if channel is not None and channel not in CHANNELS:
            raise AnalyticsValidationError("ANALYTICS_CHANNEL_INVALID")
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        clauses = ["created_at >= ?", "created_at < ?"]
        args: list[Any] = [_iso(start), _iso(end)]
        if property_id:
            clauses.append("property_id = ?"); args.append(property_id)
        if event_type:
            clauses.append("event_type = ?"); args.append(event_type)
        if channel:
            clauses.append("channel = ?"); args.append(channel)
        where = " AND ".join(clauses)
        with _LOCK:
            conn = self._connect()
            try:
                self._purge(conn, _now())
                total = conn.execute(f"SELECT COUNT(*) FROM analytics_events WHERE {where}", args).fetchone()[0]
                def grouped(sql: str) -> list[dict[str, Any]]:
                    return [dict(row) for row in conn.execute(sql.format(where=where), args).fetchall()]
                result = {
                    "range": {"from": start_date.isoformat(), "to": end_date.isoformat()},
                    "filters": {"property_id": property_id, "event_type": event_type, "channel": channel},
                    "total_events": total,
                    "by_day": grouped(f"SELECT substr(created_at,1,10) AS date, COUNT(*) AS count FROM analytics_events WHERE {where} GROUP BY date ORDER BY date"),
                    "by_property": grouped(f"SELECT property_id, event_type, COUNT(*) AS count FROM analytics_events WHERE {where} GROUP BY property_id,event_type ORDER BY property_id,event_type"),
                    "by_event_type": grouped(f"SELECT event_type, COUNT(*) AS count FROM analytics_events WHERE {where} GROUP BY event_type ORDER BY event_type"),
                    "by_channel": grouped(f"SELECT channel, COUNT(*) AS count FROM analytics_events WHERE {where} AND channel IS NOT NULL GROUP BY channel ORDER BY channel"),
                    "by_source": grouped(f"SELECT COALESCE(utm_source,referrer_host,'direct') AS source, COUNT(*) AS count FROM analytics_events WHERE {where} GROUP BY source ORDER BY source"),
                    "privacy": {"raw_ip": False, "user_agent": False, "cookies": False, "visitor_identity": False, "phone_lead_inferred": False},
                }
                conn.commit()
                self.path.chmod(0o600)
                return result
            finally:
                conn.close()
