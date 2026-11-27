"""Conservative, deterministic Candidate-to-Property matching foundation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

ALGORITHM_VERSION = "deterministic-v1"

def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())

def _equal(a: Any, b: Any) -> bool:
    return a is not None and b is not None and (_norm(a) == _norm(b) if isinstance(a, str) or isinstance(b, str) else a == b)

def _historical_values(store: Any, property_id: str, field: str) -> set[Any]:
    values = set()
    for row in store.list_field_history(property_id, field):
        for key in ("normalized_value", "raw_value"):
            value = row.get(key)
            if value is not None:
                try: values.add(value)
                except TypeError: pass
    return values

def _media_shas(store: Any, property_id: str) -> set[str]:
    return {row["media_sha256"] for row in store.list_media_history(property_id) if row.get("media_sha256")}

def _result(candidate_id: str, property_id: str | None, classification: str, score: int, matched: list[str], differing: list[str], missing: list[str], conflicting: list[str], explanation: str) -> dict[str, Any]:
    raw = f"{candidate_id}|{property_id or ''}|{ALGORITHM_VERSION}"
    return {"match_candidate_id": "match-" + hashlib.sha256(raw.encode()).hexdigest()[:24], "candidate_id": candidate_id, "property_id": property_id, "classification": classification, "score": score, "matched_signals": matched, "differing_signals": differing, "missing_signals": missing, "conflicting_signals": conflicting, "explanation": explanation, "algorithm_version": ALGORITHM_VERSION, "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}

def evaluate_candidate(store: Any, candidate: dict[str, Any], *, persist: bool = False) -> list[dict[str, Any]]:
    """Return ranked evidence only; never creates/updates a property or history."""
    facts = candidate.get("facts", {}); candidate_shas = {m.get("sha256") for m in candidate.get("media", []) if m.get("sha256")}
    results = []
    for prop in store.list_properties():
        matched, differing, missing, conflicting = [], [], [], []
        for field, label in (("address", "address"), ("area", "area"), ("property_type", "property_type"), ("width", "width"), ("length", "length"), ("land_area", "land_area"), ("construction_area", "construction_area"), ("legal_status", "legal_status"), ("road_access", "road_access")):
            candidate_value = facts.get(field); current = prop.get(field)
            historical = _historical_values(store, prop["property_id"], field)
            known = {current, *historical} - {None}
            if candidate_value is None:
                missing.append(label); continue
            if any(_equal(candidate_value, value) for value in known): matched.append(f"{label} exact")
            elif known:
                differing.append(f"{label}: candidate={candidate_value}, existing={sorted(map(str, known))}")
        overlap = candidate_shas & _media_shas(store, prop["property_id"])
        if overlap: matched.append(f"media SHA overlap ({len(overlap)})")
        if facts.get("price_vnd") is not None:
            historical_prices = {x["amount_vnd"] for x in store.list_price_history(prop["property_id"])}
            if prop.get("price") is not None: historical_prices.add(prop["price"])
            if historical_prices and facts["price_vnd"] not in historical_prices:
                differing.append(f"price changed: candidate={facts['price_vnd']}, existing/history={sorted(map(str, historical_prices))}")
        owner_area = [x for x in store.list_field_history(prop["property_id"], "land_area") if x.get("confirmed_by_owner")]
        if owner_area and facts.get("land_area") is not None and not any(_equal(facts["land_area"], x.get("normalized_value")) for x in owner_area):
            conflicting.append(f"owner-confirmed land_area differs: candidate={facts['land_area']}")
        address_exact = "address exact" in matched; dimensions = all(x in matched for x in ("width exact", "length exact")); land_exact = "land_area exact" in matched
        strong = (address_exact and dimensions and (land_exact or bool(overlap))) or (bool(overlap) and dimensions)
        medium = ("area exact" in matched and dimensions) or (address_exact and dimensions)
        if strong:
            classification = "EXISTING_PROPERTY_NEW_INFORMATION"; score = 90 + min(len(matched), 9)
        elif medium:
            classification = "POSSIBLE_DUPLICATE"; score = 55 + min(len(matched), 9)
        else:
            continue
        explanation = "; ".join(["MATCH: " + ", ".join(matched) if matched else "MATCH: none", "DIFFERENCE: " + ", ".join(differing) if differing else "DIFFERENCE: none", "MISSING: " + ", ".join(missing) if missing else "MISSING: none", "CONFLICT: " + ", ".join(conflicting) if conflicting else "CONFLICT: none"])
        result = _result(candidate["candidate_id"], prop["property_id"], classification, score, matched, differing, missing, conflicting, explanation)
        if persist: store.save_match_candidate(result)
        results.append(result)
    if not results:
        result = _result(candidate["candidate_id"], None, "NEW_PROPERTY", 0, [], [], [], [], "No credible deterministic match evidence.")
        if persist: store.save_match_candidate(result)
        results.append(result)
    return sorted(results, key=lambda x: (-x["score"], x["property_id"] or ""))
