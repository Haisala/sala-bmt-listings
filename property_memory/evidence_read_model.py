"""Private evidence and Property Intelligence read models.

Read-only: this module consumes PropertyStore records and never mutates them.
It deliberately keeps source asking prices separate from market or valuation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

PUBLIC_DATA = "PUBLIC_DATA"
PRIVATE_OWNER_DATA = "PRIVATE_OWNER_DATA"
AI_INTERNAL_ANALYSIS = "AI_INTERNAL_ANALYSIS"
KNOWN = "KNOWN"
UNKNOWN = "UNKNOWN"
CONFLICTING = "CONFLICTING"
STALE = "STALE"
_ORIGINS = {"SOURCE_RAW", "NORMALIZED", "AI_EXTRACTED", "AI_INFERRED", "OWNER_CONFIRMED"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _value(row: dict[str, Any]) -> Any:
    return row.get("normalized_value") if row.get("normalized_value") is not None else row.get("raw_value")


def _evidence(*, evidence_id: str, property_id: str, evidence_type: str,
              value: Any = None, raw_value: Any = None, normalized_value: Any = None,
              origin: str = "NORMALIZED", provenance: dict[str, Any] | None = None,
              observed_at: str | None = None, captured_at: str | None = None,
              confidence: str | None = None, rights_state: str = "UNKNOWN",
              verification_state: str = "UNVERIFIED") -> dict[str, Any]:
    if origin not in _ORIGINS:
        raise ValueError("INVALID_EVIDENCE_ORIGIN")
    return {
        "classification": PRIVATE_OWNER_DATA,
        "evidence_id": evidence_id,
        "property_id": property_id,
        "evidence_type": evidence_type,
        "source_provenance": deepcopy(provenance or {}),
        "observed_at": observed_at,
        "captured_at": captured_at or _now(),
        "freshness": "UNKNOWN",
        "value": deepcopy(value),
        "raw_value": deepcopy(raw_value),
        "normalized_value": deepcopy(normalized_value),
        "confidence": confidence or "UNKNOWN",
        "origin": origin,
        "rights_state": rights_state or "UNKNOWN",
        "verification_state": verification_state,
    }


def build_evidence(store: Any, property_id: str) -> list[dict[str, Any]]:
    """Build private evidence from existing canonical/history/provenance APIs."""
    prop = store.get(property_id)
    if prop is None:
        raise KeyError("PROPERTY_NOT_FOUND")
    out: list[dict[str, Any]] = []
    canonical_fields = ("property_type", "address", "area", "width", "length", "land_area",
                        "construction_area", "legal_status", "road_access", "floors", "rooms",
                        "interior", "features", "factual_description")
    for field in canonical_fields:
        val = prop.get(field)
        if val is not None:
            out.append(_evidence(evidence_id=f"canonical:{property_id}:{field}", property_id=property_id,
                                 evidence_type="CANONICAL_FACT", value=val, normalized_value=val,
                                 origin="NORMALIZED", verification_state="UNVERIFIED"))
    if prop.get("price") is not None:
        out.append(_evidence(evidence_id=f"canonical:{property_id}:asking_price", property_id=property_id,
                             evidence_type="ASKING_PRICE", value=prop["price"], normalized_value=prop["price"],
                             origin="NORMALIZED", verification_state="UNVERIFIED"))
    for row in store.list_field_history(property_id):
        prov = store.get_provenance(row["provenance_id"]) if row.get("provenance_id") else None
        out.append(_evidence(evidence_id=f"field:{row['event_id']}", property_id=property_id,
                             evidence_type="FIELD_OBSERVATION", value=_value(row), raw_value=row.get("raw_value"),
                             normalized_value=row.get("normalized_value"), origin=row.get("information_origin", "NORMALIZED"),
                             provenance=prov or {"appearance_id": row.get("appearance_id")},
                             observed_at=row.get("observed_at"), captured_at=row.get("recorded_at"),
                             confidence=row.get("confidence"),
                             verification_state="OWNER_CONFIRMED" if row.get("confirmed_by_owner") else "UNVERIFIED"))
    for row in store.list_price_history(property_id):
        prov = store.get_provenance(row["provenance_id"]) if row.get("provenance_id") else None
        out.append(_evidence(evidence_id=f"price:{row['event_id']}", property_id=property_id,
                             evidence_type="ASKING_PRICE", value=row.get("amount_vnd"),
                             normalized_value=row.get("amount_vnd"), origin=row.get("information_origin", "NORMALIZED"),
                             provenance=prov or {"appearance_id": row.get("appearance_id")},
                             observed_at=row.get("observed_at"), captured_at=row.get("recorded_at"),
                             confidence=row.get("confidence"),
                             verification_state="OWNER_CONFIRMED" if row.get("confirmed_by_owner") else "UNVERIFIED"))
    for row in store.list_media_history(property_id):
        out.append(_evidence(evidence_id=f"media:{row['event_id']}", property_id=property_id,
                             evidence_type="MEDIA_REFERENCE", value=row.get("media_ref"),
                             normalized_value=row.get("media_ref"), origin="SOURCE_RAW",
                             provenance={"appearance_id": row.get("appearance_id"), "media_sha256": row.get("media_sha256")},
                             captured_at=row.get("recorded_at"), rights_state=row.get("rights_status", "UNKNOWN")))
    return out


def _fact_status(observations: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> str:
    if not observations:
        return UNKNOWN
    values = {repr(x.get("value")) for x in observations if x.get("value") is not None}
    if len(values) > 1 or conflicts:
        return CONFLICTING
    return KNOWN


def build_property_intelligence(store: Any, property_id: str) -> dict[str, Any]:
    """Return a private read model. It consumes build_evidence output only."""
    prop = store.get(property_id)
    if prop is None:
        raise KeyError("PROPERTY_NOT_FOUND")
    evidence = build_evidence(store, property_id)
    conflicts = store.list_conflicts(property_id)
    facts: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if item["evidence_type"] not in {"CANONICAL_FACT", "FIELD_OBSERVATION"}:
            continue
        field = item["evidence_id"].split(":")[-1] if item["evidence_type"] == "CANONICAL_FACT" else None
        if field is None:
            continue
        facts.setdefault(field, {"value": item["value"], "origin": item["origin"], "evidence_ids": []})["evidence_ids"].append(item["evidence_id"])
    for field in {x.get("field_name") for x in store.list_field_history(property_id)} - {None}:
        rows = [x for x in evidence if x["evidence_type"] == "FIELD_OBSERVATION" and store.get_field_observation(x["evidence_id"].split(":", 1)[1]).get("field_name") == field]
        owner = [x for x in rows if x["verification_state"] == "OWNER_CONFIRMED"]
        chosen = owner[-1] if owner else (rows[-1] if rows else None)
        if chosen:
            facts[field] = {"value": chosen["value"], "origin": chosen["origin"], "evidence_ids": [x["evidence_id"] for x in rows], "status": _fact_status(rows, [c for c in conflicts if c.get("field_name") == field])}
    asking = [x for x in evidence if x["evidence_type"] == "ASKING_PRICE"]
    return {
        "classification": AI_INTERNAL_ANALYSIS,
        "property_id": property_id,
        "facts": facts,
        "asking_price": {"type": "ASKING_PRICE", "observations": asking},
        "evidence": evidence,
        "evidence_coverage": {"count": len(evidence), "fields": sorted(facts)},
        "missing_information": sorted(set((x for x in ("address", "legal_status", "land_area") if x not in facts))),
        "conflicts": deepcopy(conflicts),
        "lifecycle_state": prop.get("lifecycle_state"),
        "forbidden_roles": ["MARKET_PRICE", "TRANSACTION_PRICE", "AI_VALUATION", "DEAL_SCORE", "PUBLISHING", "MUTATION"],
    }


def public_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Allowlist-only public projection; private/internal models are rejected."""
    if value.get("classification") == AI_INTERNAL_ANALYSIS:
        raise ValueError("AI_INTERNAL_ANALYSIS_PRIVATE_ONLY")
    return {k: deepcopy(value[k]) for k in ("id", "slug", "title", "status", "price", "priceLabel", "addressText") if k in value}
