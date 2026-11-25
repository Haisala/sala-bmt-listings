"""Read-only deterministic adapter from legacy Admin Intake to Candidate."""
from __future__ import annotations
import hashlib
import unicodedata
from copy import deepcopy
from typing import Any

KNOWN_FIELDS = ("title", "property_type", "address", "area", "width", "length", "land_area", "construction_area", "price_vnd", "price_vnd_per_month", "price_type", "legal_status", "road_access", "floors", "rooms", "bedrooms", "bathrooms", "house_type", "interior", "features", "description")
_TRANSACTION_TYPES = {"sale": "SALE", "ban": "SALE", "rent": "RENT", "rental": "RENT", "cho thue": "RENT"}
_PRICE_CYCLES = {"month": "MONTH", "thang": "MONTH", "season": "SEASON", "mua": "SEASON", "year": "YEAR", "nam": "YEAR", "other": "OTHER", "khac": "OTHER"}

def _enum(value: Any, aliases: dict[str, str]) -> Any:
    if value is None or value == "":
        return None
    text = str(value).strip()
    folded = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    key = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return aliases.get(key, text.upper())

def _clean(value: Any) -> Any:
    if value is None or value == "": return None
    if isinstance(value, str): return value.strip() or None
    return value

def _source_class(source: Any) -> str:
    value = str(source or "").strip().lower()
    if value in {"cá nhân", "ca nhan", "personal"}: return "PERSONAL"
    if value in {"zalo công ty", "zalo cong ty", "company", "công ty", "cong ty"}: return "COMPANY"
    return "UNKNOWN"

def _candidate_id(intake_id: str) -> str:
    return "candidate-" + hashlib.sha256(intake_id.encode("utf-8")).hexdigest()[:24]

def _source_facts(intake: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    structured = intake.get("preliminaryFacts") or intake.get("preliminary") or intake.get("facts") or {}
    if not isinstance(structured, dict):
        structured = {}
    draft = intake.get("draft") if isinstance(intake.get("draft"), dict) else {}
    return structured, draft

def _facts(intake: dict[str, Any]) -> dict[str, Any]:
    structured, draft = _source_facts(intake)
    facts = {field: _clean(structured.get(field, draft.get(field))) for field in KNOWN_FIELDS}
    if facts["title"] is None:
        facts["title"] = _clean(structured.get("name") or draft.get("name"))
    if facts["address"] is None:
        facts["address"] = _clean(structured.get("location") or structured.get("relative_location") or draft.get("location"))
    transaction = (structured.get("transaction_type") or structured.get("transactionType")
                   or draft.get("transaction_type") or draft.get("transactionType")
                   or intake.get("transaction_type") or intake.get("transactionType"))
    current_price_type = _enum(facts.get("price_type"), _TRANSACTION_TYPES)
    transaction_type = _enum(transaction, _TRANSACTION_TYPES)
    facts["price_type"] = current_price_type or transaction_type
    cycle = structured.get("price_cycle") or structured.get("priceCycle") or draft.get("price_cycle") or intake.get("price_cycle")
    if cycle not in (None, ""):
        facts["price_cycle"] = _enum(cycle, _PRICE_CYCLES)
    return facts

def _transaction_signals(intake: dict[str, Any]) -> list[str]:
    structured, draft = _source_facts(intake)
    values = [structured.get("price_type"), structured.get("transaction_type"), structured.get("transactionType"),
              draft.get("price_type"), draft.get("transaction_type"), draft.get("transactionType"),
              intake.get("transaction_type"), intake.get("transactionType")]
    return [str(normalized) for value in values
            if (normalized := _enum(_clean(value), _TRANSACTION_TYPES)) is not None]

def to_candidate(intake: dict[str, Any]) -> dict[str, Any]:
    """Convert one intake without persistence, matching, inference or media copying."""
    intake_id = _clean(intake.get("id") or intake.get("intake_id"))
    if not intake_id: raise ValueError("INTAKE_ID_REQUIRED")
    source = _clean(intake.get("source")); facts = _facts(intake)
    raw_media = intake.get("media", []) if isinstance(intake.get("media", []), list) else []
    media = []
    for item in raw_media:
        if isinstance(item, dict):
            media.append({"media_id": _clean(item.get("mediaId") or item.get("media_id")), "filename": _clean(item.get("filename")), "path": _clean(item.get("path")), "sha256": _clean(item.get("sha256")), "bytes": item.get("bytes"), "role": _clean(item.get("role"))})
    missing = list(intake.get("missingFields", [])) if isinstance(intake.get("missingFields"), list) else []
    missing.extend(field for field, value in facts.items() if value is None and field not in missing)
    conflicts = deepcopy(intake.get("conflicts", [])) if isinstance(intake.get("conflicts", []), list) else []
    signals = set(_transaction_signals(intake))
    if len(signals) > 1:
        conflicts.append({"field": "price_type", "conflicting_signals": sorted(signals)})
    return {
        "candidate_id": _candidate_id(str(intake_id)), "intake_id": intake_id, "client_batch_id": _clean(intake.get("clientBatchId") or intake.get("client_batch_id")),
        "source": source, "source_class": _source_class(source), "source_type": _clean(intake.get("sourceType")),
        "source_group": _clean(intake.get("sourceGroup")), "received_at": _clean(intake.get("receivedAt") or intake.get("sourceDate") or intake.get("createdAt")),
        "facts": facts, "media": media,
        "fact_provenance": deepcopy(intake.get("factProvenance", {})) if isinstance(intake.get("factProvenance", {}), dict) else {},
        "raw": {"note": intake.get("note"), "text": intake.get("text") or intake.get("note"), "preliminaryFacts": deepcopy(intake.get("preliminaryFacts") or intake.get("preliminary") or intake.get("facts") or {}), "intake": deepcopy(intake)},
        "missing_fields": sorted(set(str(x) for x in missing)),
        "uncertain_fields": list(intake.get("uncertainFields", [])) if isinstance(intake.get("uncertainFields", []), list) else [],
        "conflicts": conflicts,
        "provenance": [{"intake_id": intake_id, "media_ref": item.get("path"), "media_sha256": item.get("sha256")} for item in media],
        "rights_status": intake.get("rightsStatus") if intake.get("rightsStatus") in {"PERSONAL", "COMPANY", "UNKNOWN"} else "UNKNOWN",
        "processing_metadata": deepcopy(intake.get("processingMetadata", {})) if isinstance(intake.get("processingMetadata", {}), dict) else {},
    }

def proposed_source_appearance(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"property_id": None, "source_id": candidate.get("source"), "source_class": candidate.get("source_class", "UNKNOWN"), "source_type": candidate.get("source_type"), "source_group": candidate.get("source_group"), "intake_id": candidate.get("intake_id"), "received_at": candidate.get("received_at"), "rights_status": candidate.get("rights_status", "UNKNOWN"), "processing_metadata": candidate.get("processing_metadata", {})}
