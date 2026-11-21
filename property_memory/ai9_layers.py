"""Private deterministic contracts for AI Brain layers 3-9.

These functions are decision-support foundations only. They do not mutate
PropertyStore, publish content, call an external model, or create storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import uuid
from typing import Any, Iterable

PRIVATE = "AI_INTERNAL_ANALYSIS"
CONTENT_PACKAGE = "AI_CONTENT_PACKAGE"
ALLOWED_CHANNELS = {"admin_preview", "public_web"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id(prefix: str, value: Any) -> str:
    digest = sha256(repr(value).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _facts(property_view: dict[str, Any]) -> dict[str, Any]:
    facts = property_view.get("facts") or property_view.get("candidate", {}).get("facts") or {}
    if not isinstance(facts, dict):
        raise ValueError("PROPERTY_FACTS_REQUIRED")
    return facts


def build_audience_intelligence(property_view: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
    """Return bounded audience hypotheses, never asserted buyer facts."""
    if not approved:
        raise ValueError("AUDIENCE_REQUIRES_APPROVED_FACTS")
    facts = _facts(property_view)
    hypotheses: list[dict[str, Any]] = []
    if facts.get("property_type") or facts.get("area"):
        hypotheses.append({"label": "LOCAL_RESIDENTIAL_BUYER", "basis": [x for x in ("property_type", "area") if facts.get(x)], "confidence": "LOW"})
    if facts.get("road_access"):
        hypotheses.append({"label": "ACCESS_SENSITIVE_BUYER", "basis": ["road_access"], "confidence": "LOW"})
    return {"classification": PRIVATE, "layer": 3, "status": "HYPOTHESES_ONLY" if hypotheses else "INSUFFICIENT_EVIDENCE", "audiences": hypotheses, "limitations": ["Not demographic truth", "Requires research before targeting"], "created_at": _now()}


def build_content_research(property_view: dict[str, Any], citations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Create citation-bound research; unsupported claims remain unresolved."""
    facts = _facts(property_view)
    clean = []
    for item in citations:
        if not isinstance(item, dict) or not item.get("source_id") or not item.get("claim"):
            continue
        clean.append({"source_id": str(item["source_id"]), "claim": str(item["claim"]), "rights_status": item.get("rights_status", "UNKNOWN")})
    return {"classification": PRIVATE, "layer": 4, "status": "RESEARCH_AVAILABLE" if clean else "INSUFFICIENT_EVIDENCE", "property_id": property_view.get("property_id"), "fact_keys": sorted(k for k in facts if facts[k] is not None), "citations": clean, "unverified_claims": [] if clean else ["No citable source supplied"], "created_at": _now()}


def build_strategy_director(property_view: dict[str, Any], *, audience: dict[str, Any], research: dict[str, Any], internal_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return explainable recommendations, never an approval or publish command."""
    if audience.get("classification") != PRIVATE or research.get("classification") != PRIVATE:
        raise ValueError("PRIVATE_INPUTS_REQUIRED")
    complete = bool(_facts(property_view)) and research.get("status") == "RESEARCH_AVAILABLE"
    action = "PREPARE_REVIEW" if complete else "COLLECT_EVIDENCE"
    return {"classification": PRIVATE, "layer": 5, "status": "DECISION_SUPPORT", "recommended_action": action, "reason_codes": ["CITATIONS_PRESENT" if complete else "CITATIONS_MISSING"], "approval_required": True, "created_at": _now()}


def build_content_package(property_view: dict[str, Any], *, strategy: dict[str, Any], research: dict[str, Any], rights_status: str) -> dict[str, Any]:
    """Build a private draft package; public publication is a separate gate."""
    if strategy.get("classification") != PRIVATE or research.get("classification") != PRIVATE:
        raise ValueError("PRIVATE_INPUTS_REQUIRED")
    if rights_status not in {"COMPANY", "PERSONAL", "APPROVED"}:
        raise ValueError("CONTENT_RIGHTS_REQUIRED")
    facts = _facts(property_view)
    title = facts.get("title") or facts.get("property_type") or "Bất động sản cần xác minh"
    details = [str(x) for x in (facts.get("area"), facts.get("property_type"), facts.get("road_access")) if x]
    body = f"{title}. " + (" · ".join(details) + ". " if details else "") + "Thông tin cần được Owner kiểm duyệt trước khi sử dụng."
    return {"classification": CONTENT_PACKAGE, "layer": 6, "content_package_id": _id("content", (property_view.get("property_id"), title, research.get("citations"))), "property_id": property_view.get("property_id"), "status": "DRAFT", "title": str(title), "body": body, "source_citation_ids": [x["source_id"] for x in research.get("citations", [])], "rights_status": rights_status, "created_at": _now()}


def run_gate_b(content_package: dict[str, Any], *, required_citations: bool = True) -> dict[str, Any]:
    """Critique a content package without publishing it."""
    errors = []
    if content_package.get("classification") != CONTENT_PACKAGE:
        errors.append("CONTENT_PACKAGE_REQUIRED")
    if content_package.get("rights_status") not in {"COMPANY", "PERSONAL", "APPROVED"}:
        errors.append("RIGHTS_NOT_APPROVED")
    if required_citations and not content_package.get("source_citation_ids"):
        errors.append("CITATION_REQUIRED")
    return {"classification": PRIVATE, "layer": 7, "status": "PASS" if not errors else "BLOCKED", "errors": errors, "publish_authorized": False, "created_at": _now()}


def approve_gate_b(content_package: dict[str, Any], gate_result: dict[str, Any], *, owner_id: str) -> dict[str, Any]:
    """Record an explicit private content approval without publishing."""
    if not owner_id:
        raise ValueError("OWNER_APPROVAL_REQUIRED")
    if gate_result.get("status") != "PASS":
        raise ValueError("GATE_B_NOT_PASSED")
    if content_package.get("classification") != CONTENT_PACKAGE:
        raise ValueError("CONTENT_PACKAGE_REQUIRED")
    result = dict(content_package)
    result["status"] = "OWNER_APPROVED"
    result["approved_by"] = owner_id
    result["approved_at"] = _now()
    result["publish_authorized"] = False
    return result


def summarize_performance(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize sanitized first-party events; no raw request data accepted."""
    counts: dict[str, int] = {}
    accepted = 0
    for event in events:
        if not isinstance(event, dict) or not event.get("event") or any(k in event for k in ("ip", "cookie", "authorization", "password", "request_body")):
            continue
        name = str(event["event"])
        counts[name] = counts.get(name, 0) + 1
        accepted += 1
    return {"classification": PRIVATE, "layer": 8, "status": "OBSERVATIONAL", "accepted_events": accepted, "events": counts, "created_at": _now()}


def evaluate_learning(*, predictions: Iterable[dict[str, Any]], outcomes: Iterable[dict[str, Any]], model_version: str) -> dict[str, Any]:
    """Evaluate a version without self-updating or changing production behavior."""
    if not model_version:
        raise ValueError("MODEL_VERSION_REQUIRED")
    outcome_map = {str(x.get("prediction_id")): x for x in outcomes if isinstance(x, dict) and x.get("prediction_id")}
    compared = 0
    correct = 0
    for prediction in predictions:
        if not isinstance(prediction, dict) or str(prediction.get("prediction_id")) not in outcome_map:
            continue
        compared += 1
        if prediction.get("label") == outcome_map[str(prediction["prediction_id"])].get("label"):
            correct += 1
    return {"classification": PRIVATE, "layer": 9, "status": "EVALUATION_ONLY", "model_version": model_version, "compared": compared, "accuracy": (correct / compared if compared else None), "promote": False, "rollback_required": False, "created_at": _now()}


OWNER_CONFIRMED_TEST_INTAKE = "intake-df36c1b62f7f6fe61d42244f"
OWNER_CONFIRMED_TEST_REVIEW = "review-6c508fe1ab9c0638a882a604"
OWNER_CONFIRMED_TEST_COMMENT = "6936076053"


def run_ai9_preview(package: dict[str, Any], *, performance_events: Iterable[dict[str, Any]] = (), predictions: Iterable[dict[str, Any]] = (), outcomes: Iterable[dict[str, Any]] = (), model_version: str = "not-promoted", owner_confirmed_test: bool = False, owner_confirmation_scope: str = "", environment: str = "", run_post_content_evaluation: bool = False) -> dict[str, Any]:
    """Run the complete private AI9 chain without persistence or side effects."""
    candidate = package.get("candidate") or {}
    property_view = {"property_id": (package.get("property_intelligence") or {}).get("property_id"), "facts": candidate.get("facts") or {}}
    citations = candidate.get("provenance", {}).get("citations", []) if isinstance(candidate.get("provenance"), dict) else []
    if owner_confirmed_test and (environment != "staging" or package.get("candidate", {}).get("intake_id") != OWNER_CONFIRMED_TEST_INTAKE or package.get("review_id") != OWNER_CONFIRMED_TEST_REVIEW or owner_confirmation_scope != OWNER_CONFIRMED_TEST_COMMENT or package.get("state") not in {"PENDING_REVIEW", "EDITED"}):
        raise ValueError("OWNER_CONFIRMED_TEST_SCOPE_INVALID")
    run_id = "ai-run-" + uuid.uuid4().hex[:24]
    facts = candidate.get("facts") or {}
    research = build_content_research(property_view, citations)
    evaluate_post_content = run_post_content_evaluation or bool(performance_events or predictions or outcomes)
    if evaluate_post_content:
        performance = summarize_performance(performance_events)
        learning = evaluate_learning(predictions=predictions, outcomes=outcomes, model_version=model_version)
    else:
        performance = {"classification": PRIVATE, "layer": 8, "status": "DEFERRED_POST_CONTENT", "reason": "NO_EVALUATION_INPUTS", "created_at": _now()}
        learning = {"classification": PRIVATE, "layer": 9, "status": "DEFERRED_POST_CONTENT", "reason": "NO_EVALUATION_INPUTS", "created_at": _now()}
    layers: dict[str, Any] = {"facts": {"classification": PRIVATE, "layer": 1, "status": "PASS" if facts else "INSUFFICIENT_EVIDENCE"}, "provenance": {"classification": PRIVATE, "layer": 2, "status": "PASS" if citations else "INSUFFICIENT_EVIDENCE", "citation_count": len(citations)}, "audience": None, "research": research, "strategy": None, "content": None, "gate_b": None, "performance": performance, "learning": learning}
    errors: list[str] = []
    try:
        layers["audience"] = build_audience_intelligence(property_view, approved=owner_confirmed_test or package.get("state") == "APPROVED")
        layers["strategy"] = build_strategy_director(property_view, audience=layers["audience"], research=research, internal_analysis=package.get("internal_analysis"))
        if (owner_confirmed_test or package.get("state") == "APPROVED") and research.get("status") == "RESEARCH_AVAILABLE":
            layers["content"] = build_content_package(property_view, strategy=layers["strategy"], research=research, rights_status=candidate.get("rights_status", "UNKNOWN"))
            layers["gate_b"] = run_gate_b(layers["content"])
    except ValueError as exc:
        errors.append(str(exc))
    for name in ("strategy", "content", "gate_b"):
        if layers[name] is None:
            layers[name] = {"classification": PRIVATE, "layer": {"strategy": 5, "content": 6, "gate_b": 7}[name], "status": "BLOCKED", "reason": errors[-1] if errors else "PREREQUISITE_NOT_MET"}
    if evaluate_post_content:
        layers["performance"]["status"] = "PASS" if layers["performance"].get("accepted_events") else "INSUFFICIENT_EVIDENCE"
        layers["learning"]["status"] = "PASS" if layers["learning"].get("compared") else "INSUFFICIENT_EVIDENCE"
    if layers["audience"] is not None:
        layers["audience"]["metadata_status"] = layers["audience"].get("status")
        layers["audience"]["status"] = "PASS" if owner_confirmed_test or package.get("state") == "APPROVED" else "BLOCKED"
    if layers["strategy"] is not None:
        layers["strategy"]["metadata_status"] = layers["strategy"].get("status")
        layers["strategy"]["status"] = "PASS" if research.get("status") == "RESEARCH_AVAILABLE" else "INSUFFICIENT_EVIDENCE"
    for layer in layers.values():
        layer["acceptance_status"] = layer.get("status") if layer.get("status") in {"PASS", "BLOCKED", "INSUFFICIENT_EVIDENCE", "DEFERRED_POST_CONTENT"} else "BLOCKED"
    facts_revision_id = f"facts-revision-{package.get('review_id')}-r{package.get('review_revision', 1)}"
    return {"classification": PRIVATE, "ai_run_id": run_id, "review_id": package.get("review_id"), "review_state": package.get("state"), "owner_confirmed_test": owner_confirmed_test, "facts_revision_id": facts_revision_id, "lineage": {"intake_id": candidate.get("intake_id"), "ai_run_id": run_id, "facts_revision_id": facts_revision_id, "content_batch_id": None, "content_variant_ids": []}, "layers": layers, "errors": errors, "mutated_property_store": False, "published": False, "created_at": _now()}


def public_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Fail closed for all private AI9 artifacts."""
    if value.get("classification") in {PRIVATE, CONTENT_PACKAGE} or value.get("layer") in range(3, 10):
        raise ValueError("AI9_PRIVATE_ONLY")
    return {k: value[k] for k in ("id", "title") if k in value}
