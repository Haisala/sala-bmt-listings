"""Explicitly approved Property Memory mutations using the M2 boundary."""
from __future__ import annotations
import hashlib
import unicodedata
import json
from typing import Any, Callable
from .store import PropertyStore, utc_now

DECISIONS = {"CREATE_NEW_PROPERTY", "ATTACH_TO_EXISTING_PROPERTY", "NO_MUTATION", "NEEDS_REVIEW"}

def _canonical_price_type(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold().replace("đ", "d"))
    key = "".join(ch for ch in text if not unicodedata.combining(ch))
    return {"sale": "SALE", "ban": "SALE", "rent": "RENT", "rental": "RENT", "cho thue": "RENT"}.get(key, key.upper())

class ReliabilityBoundary:
    """Thin adapter to existing M2 audit/error helpers; no second queue."""
    def __init__(self, audit: Callable[..., None], enqueue: Callable[..., dict]):
        self.audit = audit; self.enqueue = enqueue

def default_reliability() -> ReliabilityBoundary:
    from admin.admin_server import append_audit, enqueue_error
    return ReliabilityBoundary(append_audit, enqueue_error)

def run_with_m2_retry(operation_id: str, intake_id: str | None, operation: Callable[[], Any], *, reliability: ReliabilityBoundary | None = None, max_attempts: int | None = None) -> Any:
    """Use the existing M2 retry limit/audit semantics around a domain call."""
    reliability = reliability or default_reliability()
    if max_attempts is None:
        from admin.admin_server import RETRY_LIMIT
        max_attempts = RETRY_LIMIT
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            value = operation()
            reliability.audit("property_mutation", intake_id, "succeeded", None, attempt, operation_id=operation_id)
            return value
        except Exception as exc:
            last = exc
            status = "failed" if attempt >= max_attempts else "retrying"
            reliability.audit("property_mutation", intake_id, status, "PROPERTY_MUTATION_FAILED", attempt, operation_id=operation_id)
            if status == "failed":
                reliability.enqueue("property_mutation", intake_id, "PROPERTY_MUTATION_TERMINAL", str(exc)[:120], attempt=attempt, operation_id=operation_id)
                raise
    raise last  # type: ignore[misc]

def _op_id(key: str) -> str:
    return "op-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

def _insert_appearance(conn, prop_id, candidate, operation_id):
    now = utc_now(); appearance_id = "appearance-" + operation_id[3:]
    conn.execute("""INSERT INTO source_appearances
    (appearance_id, property_id, source_id, source_class, source_type, source_group, intake_id,
     received_at, source_timestamp, rights_status, processing_metadata_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (appearance_id, prop_id, candidate.get("source"), candidate.get("source_class", "UNKNOWN"), candidate.get("source_type"), candidate.get("source_group"), candidate.get("intake_id"), candidate.get("received_at") or now, None, candidate.get("rights_status", "UNKNOWN"), json.dumps(candidate.get("processing_metadata", {}), ensure_ascii=False), now))
    return appearance_id

def _insert_property(conn, store, candidate, operation_id):
    prop_id = "property-" + operation_id[3:]
    facts = candidate.get("facts", {}); now = utc_now()
    price_type = _canonical_price_type(facts.get("price_type") or facts.get("transaction_type"))
    price = facts.get("price_vnd_per_month") if price_type == "RENT" else facts.get("price_vnd")
    attributes = dict(candidate.get("attributes") or {})
    if price_type in {"SALE", "RENT"}:
        attributes["price_type"] = price_type
    values = {"title": facts.get("title") or candidate.get("raw", {}).get("text") or candidate["candidate_id"], "property_type": facts.get("property_type"), "address": facts.get("address"), "area": facts.get("area"), "width": facts.get("width"), "length": facts.get("length"), "land_area": facts.get("land_area"), "construction_area": facts.get("construction_area"), "price": price, "legal_status": facts.get("legal_status"), "road_access": facts.get("road_access"), "factual_description": facts.get("description"), "attributes": attributes, "lifecycle_state": "NEEDS_REVIEW", "property_revision": 1}
    record = store._record(values, prop_id, now, now, "NEEDS_REVIEW")
    cols = ", ".join(record); marks = ", ".join("?" for _ in record)
    conn.execute(f"INSERT INTO canonical_properties ({cols}) VALUES ({marks})", tuple(record.values()))
    return prop_id

def _append_observations(conn, store, prop_id, appearance_id, candidate):
    facts = candidate.get("facts", {}); now = utc_now()
    for field in ("address", "area", "width", "length", "land_area", "construction_area", "legal_status", "road_access", "property_type", "price_type", "price_vnd_per_month"):
        if facts.get(field) is not None:
            event_id = f"field-{appearance_id}-{field}"
            conn.execute("""INSERT INTO property_field_history
            (event_id, property_id, field_name, raw_value_json, normalized_value_json, information_origin, appearance_id, confidence, confirmed_by_owner, recorded_at)
            VALUES (?, ?, ?, ?, ?, 'NORMALIZED', ?, ?, 0, ?)""", (event_id, prop_id, field, json.dumps(facts[field], ensure_ascii=False), json.dumps(facts[field], ensure_ascii=False), appearance_id, 1.0, now))
    price_type = _canonical_price_type(facts.get("price_type") or facts.get("transaction_type"))
    price_amount = facts.get("price_vnd_per_month") if price_type == "RENT" else facts.get("price_vnd")
    if price_amount is not None:
        conn.execute("""INSERT INTO property_price_history
        (event_id, property_id, amount_vnd, currency, appearance_id, information_origin, recorded_at, confirmed_by_owner)
        VALUES (?, ?, ?, 'VND', ?, 'NORMALIZED', ?, 0)""", (f"price-{appearance_id}", prop_id, price_amount, appearance_id, now))
    for index, media in enumerate(candidate.get("media", [])):
        if media.get("path"):
            conn.execute("""INSERT INTO property_media_history
            (event_id, property_id, appearance_id, media_ref, media_sha256, role, recorded_at, rights_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (f"media-{appearance_id}-{index}", prop_id, appearance_id, media["path"], media.get("sha256"), media.get("role"), now, candidate.get("rights_status", "UNKNOWN")))

def apply_approved_mutation(store: PropertyStore, candidate: dict[str, Any], decision: str, *, idempotency_key: str, target_property_id: str | None = None, reliability: ReliabilityBoundary | None = None, failure: Callable[[str], None] | None = None) -> dict[str, Any]:
    if decision not in DECISIONS: raise ValueError("INVALID_MUTATION_DECISION")
    reliability = reliability or default_reliability(); operation_id = _op_id(idempotency_key)
    if decision in {"NO_MUTATION", "NEEDS_REVIEW"}: return {"operation_id": operation_id, "status": "accepted", "mutated": False}
    existing = store.connection.execute("SELECT * FROM property_mutation_operations WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if existing and existing["status"] == "succeeded":
        appearance_id = "appearance-" + operation_id[3:]
        appearance = store.get_source_appearance(appearance_id)
        return {"operation_id": operation_id, "status": "succeeded", "mutated": False, "idempotent_replay": True, "property_id": appearance.get("property_id") if appearance else None, "appearance_id": appearance_id}
    reliability.audit("property_mutation", candidate.get("intake_id"), "accepted", None, 1, operation_id=operation_id, idempotency_key=idempotency_key)
    try:
        with store.connection:
            now = utc_now()
            store.connection.execute("""INSERT INTO property_mutation_operations
            (operation_id, idempotency_key, candidate_id, decision, target_property_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'accepted', ?, ?) ON CONFLICT(idempotency_key) DO UPDATE SET updated_at=excluded.updated_at""", (operation_id, idempotency_key, candidate["candidate_id"], decision, target_property_id, now, now))
            if failure: failure("before_mutation")
            if decision == "CREATE_NEW_PROPERTY": prop_id = _insert_property(store.connection, store, candidate, operation_id)
            else:
                if not target_property_id or store.get(target_property_id) is None: raise ValueError("TARGET_PROPERTY_REQUIRED")
                prop_id = target_property_id
            appearance_id = _insert_appearance(store.connection, prop_id, candidate, operation_id)
            _append_observations(store.connection, store, prop_id, appearance_id, candidate)
            if failure: failure("before_postcondition")
            if store.connection.execute("SELECT COUNT(*) FROM source_appearances WHERE appearance_id = ?", (appearance_id,)).fetchone()[0] != 1: raise RuntimeError("POSTCONDITION_FAILED")
            store.connection.execute("UPDATE property_mutation_operations SET status='succeeded', updated_at=? WHERE operation_id=?", (utc_now(), operation_id))
        reliability.audit("property_mutation", candidate.get("intake_id"), "succeeded", None, 1, operation_id=operation_id, idempotency_key=idempotency_key)
        return {"operation_id": operation_id, "status": "succeeded", "property_id": prop_id, "appearance_id": appearance_id, "mutated": True}
    except Exception as exc:
        code = "PROPERTY_MUTATION_FAILED"; safe = str(exc)[:120]
        reliability.enqueue("property_mutation", candidate.get("intake_id"), code, safe, operation_id=operation_id, idempotency_key=idempotency_key)
        reliability.audit("property_mutation", candidate.get("intake_id"), "failed", code, 1, operation_id=operation_id, idempotency_key=idempotency_key)
        raise
