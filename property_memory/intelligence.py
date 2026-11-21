"""Offline-safe AI Property Intelligence contract and provider boundary."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

ORIGINS = {"AI_EXTRACTED", "AI_INFERRED"}
CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

class PropertyIntelligenceProvider(Protocol):
    provider_name: str
    def extract(self, source: dict[str, Any]) -> dict[str, Any]: ...

@dataclass(frozen=True)
class IntelligenceResult:
    intake_id: str
    fields: tuple[dict[str, Any], ...]
    missing_fields: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    rights_status: str
    raw_source: dict[str, Any]
    provider_name: str
    metadata: dict[str, Any] | None = None

def validate_result(value: dict[str, Any], *, intake_id: str, provider_name: str = "unknown") -> IntelligenceResult:
    if not isinstance(value, dict) or not isinstance(value.get("fields", []), list):
        raise ValueError("INVALID_INTELLIGENCE_RESULT")
    fields = []
    for field in value["fields"]:
        if not isinstance(field, dict) or not field.get("field") or field.get("origin") not in ORIGINS:
            raise ValueError("INVALID_INTELLIGENCE_FIELD")
        if field.get("confidence") not in CONFIDENCE:
            raise ValueError("INVALID_INTELLIGENCE_CONFIDENCE")
        if not isinstance(field.get("evidence"), list) or not field["evidence"]:
            raise ValueError("MISSING_FIELD_EVIDENCE")
        if not isinstance(field.get("provenance"), dict) or field["provenance"].get("intake_id") != intake_id:
            raise ValueError("INVALID_FIELD_PROVENANCE")
        fields.append({"field": field["field"], "raw_value": field.get("raw_value"), "normalized_value": field.get("normalized_value"), "origin": field["origin"], "confidence": field["confidence"], "evidence": deepcopy(field["evidence"]), "provenance": deepcopy(field["provenance"]), "warnings": list(field.get("warnings", []))})
    rights = value.get("rights_status", "UNKNOWN")
    if rights not in {"PERSONAL", "COMPANY", "UNKNOWN"}: raise ValueError("INVALID_RIGHTS_STATUS")
    missing = value.get("missing_fields", [])
    conflicts = value.get("conflicts", [])
    if not isinstance(missing, list) or not all(isinstance(x, str) for x in missing): raise ValueError("INVALID_MISSING_FIELDS")
    if not isinstance(conflicts, list) or not all(isinstance(x, dict) for x in conflicts): raise ValueError("INVALID_CONFLICTS")
    return IntelligenceResult(intake_id, tuple(fields), tuple(missing), tuple(deepcopy(conflicts)), rights, deepcopy(value.get("raw_source", {})), provider_name, deepcopy(value.get("metadata", {})))

def extract(provider: PropertyIntelligenceProvider, intake: dict[str, Any]) -> IntelligenceResult:
    intake_id = intake.get("id") or intake.get("intake_id")
    if not intake_id: raise ValueError("INTAKE_ID_REQUIRED")
    return validate_result(provider.extract(deepcopy(intake)), intake_id=str(intake_id), provider_name=getattr(provider, "provider_name", "unknown"))

def enrich_candidate(candidate: dict[str, Any], result: IntelligenceResult) -> dict[str, Any]:
    if candidate.get("intake_id") != result.intake_id: raise ValueError("INTAKE_CANDIDATE_MISMATCH")
    enriched = deepcopy(candidate)
    enriched["intelligence"] = {"provider": result.provider_name, "fields": [deepcopy(x) for x in result.fields], "missing_fields": list(result.missing_fields), "conflicts": deepcopy(list(result.conflicts)), "rights_status": result.rights_status, "metadata": deepcopy(result.metadata or {})}
    enriched["missing_fields"] = sorted(set(enriched.get("missing_fields", [])) | set(result.missing_fields))
    enriched["conflicts"] = enriched.get("conflicts", []) + list(result.conflicts)
    facts_provenance = enriched.setdefault("facts_provenance", {})
    aliases = {
        "asking_price_vnd": "price_vnd", "monthly_price_vnd": "price_vnd_per_month",
        "land_area_sqm": "land_area", "construction_area_sqm": "construction_area",
    }
    for field in result.fields:
        # Additive proposal only; provenance/origin remains in intelligence.fields.
        if field["normalized_value"] is not None:
            source_name = field["field"]
            target = source_name if source_name in enriched.setdefault("facts", {}) else aliases.get(source_name)
            if target and enriched["facts"].get(target) in (None, ""):
                enriched["facts"][target] = field["normalized_value"]
                facts_provenance[target] = {"source_field": source_name, "origin": field["origin"], "confidence": field["confidence"], "evidence": deepcopy(field["evidence"]), "intake_id": result.intake_id}
    return enriched
