"""Owner-supplied provenance and rights evidence contract."""
from __future__ import annotations

from typing import Any

ALLOWED_RIGHTS = {"COMPANY", "PERSONAL", "APPROVED"}


def validate_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    citations = payload.get("citations")
    rights_status = payload.get("rights_status")
    if not isinstance(citations, list) or not citations:
        raise ValueError("CITATIONS_REQUIRED")
    if rights_status not in ALLOWED_RIGHTS:
        raise ValueError("CONTENT_RIGHTS_REQUIRED")
    clean = []
    for item in citations:
        if not isinstance(item, dict) or not item.get("source_id") or not item.get("claim"):
            raise ValueError("CITATION_SCHEMA_INVALID")
        item_rights = item.get("rights_status", rights_status)
        if item_rights not in ALLOWED_RIGHTS:
            raise ValueError("CITATION_RIGHTS_INVALID")
        clean.append({"source_id": str(item["source_id"]), "claim": str(item["claim"]), "rights_status": item_rights})
    return {"citations": clean, "rights_status": rights_status, "source_references": payload.get("source_references") or []}
