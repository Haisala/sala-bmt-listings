"""Private, deterministic AI Copy Studio preview contract."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any
from .usage_ledger import compute_usd, normalize_usage, official_pricing_catalog
from .providers.openai_provider import CONTENT_NORMALIZED_FACT_FIELDS


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id(prefix: str, value: str) -> str:
    return f"{prefix}-" + sha256(value.encode("utf-8")).hexdigest()[:24]


GENERATIVE_MODE = "GENERATIVE_INTERNAL_DRAFT"
GENERATIVE_PROMPT_VERSION = "content-studio-v1"
ANGLES = ("NEEDS_USE", "ASSET_VALUE", "STORYTELLING")
CONTENT_BATCH_OUTPUT_COUNT = 5


def build_generative_content_batch(*, generation_id: str, facts: dict[str, Any], provenance: dict[str, Any], rights_status: str | None,
                                   media: list[dict[str, Any]] | None, provider: Any | None, model: str,
                                   usage_sink: Any | None = None, review_id: str | None = None,
                                   source_text: str = "") -> dict[str, Any]:
    """One injected provider call producing one private five-output Content batch."""
    if provider is None:
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_MODEL_NOT_CONFIGURED"], "published": False, "variants": []}
    if not generation_id or not isinstance(facts, dict) or not isinstance(provenance, dict):
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_INPUT_INVALID"], "published": False, "variants": []}
    if rights_status not in {"COMPANY", "PERSONAL", "APPROVED"}:
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_RIGHTS_REQUIRED"], "published": False, "variants": []}
    if not isinstance(source_text, str) or len(source_text) > 320:
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_SOURCE_LIMIT_INVALID"], "published": False, "variants": []}
    payload = {"generation_id": generation_id, "model": model, "facts": {k: v for k, v in facts.items() if v not in (None, "")},
               "provenance": provenance, "rights_status": rights_status, "media": [], "media_analysis": "NOT_SENT",
               "source_text": source_text, "output_count": CONTENT_BATCH_OUTPUT_COUNT,
               "output_schema": "exactly_five_content_variants"}
    seed = f"{generation_id}:{model}:content-batch-v1"
    receipt_id = _id("content-receipt", seed)
    provider_name = getattr(provider, "provider_name", "injected")

    def record_attempt(*, outcome: str, usage: dict[str, Any] | None, request_id: str | None) -> dict[str, Any] | None:
        if usage_sink is None:
            return None
        try:
            return usage_sink.record(receipt_id=receipt_id, intake_id=provenance.get("intake_id"), review_id=review_id,
                                     stage="content_generation", operation="content_processor.generate", provider=provider_name,
                                     model=model, outcome=outcome, usage=usage, provider_request_id=request_id,
                                     idempotency_key=generation_id, ai_run_id=generation_id,
                                     pricing=official_pricing_catalog().get(model))
        except Exception:
            return None

    def failed_response(reason_codes: list[str], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        recorded = record_attempt(outcome="FAILED", usage=response_usage, request_id=response_request_id)
        required_usage = ("input_tokens", "output_tokens", "total_tokens")
        usage_complete = isinstance(response_usage, dict) and all(
            isinstance(response_usage.get(key), int) and not isinstance(response_usage.get(key), bool)
            and response_usage[key] >= 0 for key in required_usage
        )
        calculated = compute_usd(response_usage, official_pricing_catalog().get(model)) if usage_complete else None
        receipt = {
            "receipt_id": receipt_id,
            "generation_id": generation_id,
            "review_id": review_id,
            "intake_id": provenance.get("intake_id"),
            "provider": provider_name,
            "model": model,
            "request_id": response_request_id,
            "usage": normalize_usage(response_usage),
            "usage_status": "PROVIDER_RETURNED_USAGE" if usage_complete else (
                "USAGE_INCOMPLETE" if response_usage is not None else "USAGE_UNKNOWN"),
            "cost_status": ((recorded or {}).get("status") or (calculated or {}).get("status") or "USAGE_UNKNOWN")
                if usage_complete else ("USAGE_INCOMPLETE" if response_usage is not None else "USAGE_UNKNOWN"),
            "estimated_cost_usd": ((recorded or {}).get("usd_cost") if recorded else (calculated or {}).get("usd_cost"))
                if usage_complete else None,
            "billing_reconciled_usd": None,
            "ledger_status": "RECORDED" if recorded else ("WRITE_FAILED" if usage_sink is not None else "NOT_CONFIGURED"),
            "outcome": "FAILED_VALIDATION",
        }
        return {"status": "BLOCKED", "reason_codes": reason_codes, "published": False,
                "mutated_property_store": False, "variants": [], "receipt": receipt, **(extra or {})}

    try:
        response = provider.generate(payload)
    except Exception as exc:
        record_attempt(outcome="FAILED", usage=None, request_id=None)
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_PROVIDER_ERROR", type(exc).__name__], "published": False, "variants": []}
    variants = response.get("variants") if isinstance(response, dict) else None
    normalized_facts = response.get("normalized_facts") if isinstance(response, dict) else None
    missing_fields = response.get("missing_fields") if isinstance(response, dict) else None
    conflicts = response.get("conflicts") if isinstance(response, dict) else None
    response_usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else None
    response_request_id = response.get("provider_request_id") if isinstance(response, dict) and isinstance(response.get("provider_request_id"), str) else None
    if (not isinstance(variants, list) or len(variants) != CONTENT_BATCH_OUTPUT_COUNT
            or not isinstance(normalized_facts, list) or not isinstance(missing_fields, list)
            or not isinstance(conflicts, list)):
        return failed_response(["CONTENT_SCHEMA_INVALID"])
    source_fact_keys = {k for k, v in facts.items() if v not in (None, "")}
    submitted_fact_keys = response.get("input_fact_keys")
    if submitted_fact_keys is None:
        # Injected offline providers predate this metadata; live provider attaches
        # the exact, budget-filtered keys in its outbound request.
        allowed = source_fact_keys
    elif (not isinstance(submitted_fact_keys, list)
          or not all(isinstance(key, str) for key in submitted_fact_keys)
          or len(submitted_fact_keys) != len(set(submitted_fact_keys))
          or not set(submitted_fact_keys).issubset(source_fact_keys)):
        return failed_response(["CONTENT_FACTS_SCHEMA_INVALID"])
    else:
        allowed = set(submitted_fact_keys)
    normalized_keys: set[str] = set()
    for item in normalized_facts:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("field", "raw_value", "normalized_value", "evidence")):
            return failed_response(["CONTENT_FACTS_SCHEMA_INVALID"])
        field = item["field"]
        evidence = item["evidence"].strip()
        if field not in CONTENT_NORMALIZED_FACT_FIELDS or field in normalized_keys or not evidence or evidence not in source_text:
            return failed_response(["CONTENT_FACTS_EVIDENCE_INVALID"])
        normalized_keys.add(field)
        if field in facts and facts[field] not in (None, "") and str(facts[field]).strip() != item["normalized_value"].strip():
            conflicts.append(field)
    if conflicts:
        return failed_response(["CONTENT_SOURCE_CONFLICT"], extra={
            "normalized_facts": normalized_facts, "conflicts": sorted(set(str(x) for x in conflicts))})
    bodies = []
    for item in variants:
        if not isinstance(item, dict) or not isinstance(item.get("body"), str) or not item["body"].strip():
            return failed_response(["CONTENT_SCHEMA_INVALID"])
        facts_used = item.get("facts_used")
        if (not isinstance(facts_used, list) or not all(isinstance(value, str) for value in facts_used)
                or len(facts_used) != len(set(facts_used)) or not set(facts_used).issubset(allowed)):
            return failed_response(["CONTENT_FACTS_UNGROUNDED"])
        if not all(isinstance(item.get(k), list) for k in ("citations_used", "unknowns", "warnings")):
            return failed_response(["CONTENT_METADATA_INVALID"])
        bodies.append(item["body"])
    indices = [item.get("variant_index") for item in variants]
    if any(type(index) is not int for index in indices) or sorted(indices) != list(range(1, CONTENT_BATCH_OUTPUT_COUNT + 1)):
        return failed_response(["CONTENT_VARIANT_INDEX_INVALID"])
    if len(set(bodies)) != CONTENT_BATCH_OUTPUT_COUNT:
        return failed_response(["CONTENT_NOT_DISTINCT"])
    batch_id = _id("content-batch", seed)
    usage = response_usage
    request_id = response_request_id
    usage_receipt = record_attempt(outcome="SUCCEEDED", usage=usage, request_id=request_id)
    if usage_receipt is None:
        return {"status": "BLOCKED", "reason_codes": ["USAGE_RECEIPT_WRITE_FAILED"], "published": False, "variants": []}
    output = []
    for index, item in enumerate(variants, 1):
        output.append({**item, "content_variant_id": _id("content-variant", f"{seed}:{index}"), "variant_index": index, "status": "DRAFT", "visibility": "INTERNAL_ONLY", "generation_id": generation_id, "model": model, "content_batch_id": batch_id, "receipt_id": receipt_id})
    tokens = usage_receipt.get("tokens") if isinstance(usage_receipt.get("tokens"), dict) else {}
    receipt = {"receipt_id": receipt_id, "generation_id": generation_id, "review_id": review_id,
               "intake_id": provenance.get("intake_id"), "provider": provider_name, "model": model,
               "request_id": request_id, "usage": tokens,
               "usage_status": "PROVIDER_RETURNED_USAGE" if usage is not None else "USAGE_UNAVAILABLE",
               "cost_status": usage_receipt.get("status", "PRICE_NOT_VERIFIED"),
               "estimated_cost_usd": usage_receipt.get("usd_cost"), "content_batch_id": batch_id,
               "content_variant_ids": [x["content_variant_id"] for x in output], "media_analysis": "NOT_SENT"}
    return {"status": "PASS", "generation_id": generation_id, "content_batch_id": batch_id,
            "content_variant_ids": receipt["content_variant_ids"], "variants": output, "receipt": receipt,
            "normalized_facts": normalized_facts, "missing_fields": missing_fields, "conflicts": [],
            "published": False, "mutated_property_store": False, "media_analysis": "NOT_SENT"}


def build_generative_internal_draft(*, ai_run: dict[str, Any], facts: dict[str, Any], provenance_status: str,
                                    rights_status: str | None, provider: Any | None, model: str,
                                    usage_sink: Any | None = None) -> dict[str, Any]:
    """Opt-in, injected-provider pathway; never enabled by the normal preview route."""
    if provider is None:
        return {"status": "BLOCKED", "reason_codes": ["CONTENT_MODEL_NOT_CONFIGURED"], "mode": GENERATIVE_MODE,
                "mutated_property_store": False, "published": False, "variants": []}
    if not isinstance(facts, dict) or not ai_run.get("facts_revision_id"):
        return {"status": "BLOCKED", "reason_codes": ["FACTS_REVISION_REQUIRED"], "mode": GENERATIVE_MODE,
                "mutated_property_store": False, "published": False, "variants": []}
    payload = {"model": model, "prompt_version": GENERATIVE_PROMPT_VERSION, "mode": GENERATIVE_MODE,
               "facts_revision_id": ai_run["facts_revision_id"], "facts": {k: v for k, v in facts.items() if v not in (None, "")},
               "provenance_status": provenance_status, "rights_status": rights_status or "UNKNOWN",
               "angles": list(ANGLES), "output_schema": "exactly_three_variants"}
    seed = f"{ai_run.get('ai_run_id')}:{ai_run['facts_revision_id']}:{model}:{GENERATIVE_PROMPT_VERSION}"
    receipt_id = _id("content-receipt", seed)
    provider_name = getattr(provider, "provider_name", "injected")
    generation = {"generation_id": ai_run.get("ai_run_id"), "provider": provider_name, "model": model,
                  "request_id": None, "receipt_id": receipt_id, "usage": None,
                  "usage_status": "USAGE_UNAVAILABLE", "cost_status": "USAGE_UNAVAILABLE",
                  "cost_cap_usd": "0.01"}

    def record_attempt(outcome: str, usage: dict[str, Any] | None, request_id: str | None) -> bool:
        generation.update({"request_id": request_id, "usage": usage,
                           "usage_status": "PROVIDER_RETURNED_USAGE" if usage is not None else "USAGE_UNAVAILABLE"})
        if usage_sink is None:
            generation["cost_status"] = "USAGE_SINK_UNAVAILABLE"
            return False
        try:
            recorded = usage_sink.record(
                receipt_id=receipt_id, intake_id=(ai_run.get("lineage") or {}).get("intake_id"),
                review_id=ai_run.get("review_id"), ai_run_id=ai_run.get("ai_run_id"),
                stage="content_generation", operation="copy_studio.generate", provider=provider_name,
                model=model, outcome=outcome, usage=usage, provider_request_id=request_id,
                idempotency_key=receipt_id, pricing=official_pricing_catalog().get(model))
            generation["cost_status"] = recorded.get("status", "PRICE_NOT_VERIFIED")
            generation["estimated_cost_usd"] = recorded.get("usd_cost")
            return True
        except Exception:
            generation["cost_status"] = "USAGE_RECEIPT_WRITE_FAILED"
            return False

    def blocked(reason: str, usage: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
        record_attempt("FAILED", usage, request_id)
        return {"status": "BLOCKED", "reason_codes": [reason], "mode": GENERATIVE_MODE,
                "generation": generation, "mutated_property_store": False, "published": False, "variants": []}

    try:
        response = provider.generate(payload)
    except Exception as exc:
        provider_code = getattr(exc, "code", None)
        reason = provider_code if isinstance(provider_code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", provider_code) else type(exc).__name__
        diagnostics = getattr(exc, "diagnostics", {})
        if isinstance(diagnostics, dict):
            status = diagnostics.get("http_status")
            if isinstance(status, int) and 100 <= status <= 599:
                generation["provider_http_status"] = status
            error_code = diagnostics.get("error_code")
            if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", error_code):
                generation["provider_error_code"] = error_code
            response_status = diagnostics.get("response_status")
            if isinstance(response_status, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", response_status):
                generation["provider_response_status"] = response_status
            incomplete_reason = diagnostics.get("incomplete_reason")
            if isinstance(incomplete_reason, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", incomplete_reason):
                generation["provider_incomplete_reason"] = incomplete_reason
            output_types = diagnostics.get("output_item_types")
            if isinstance(output_types, list):
                generation["provider_output_item_types"] = [x for x in output_types if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", x)][:24]
            request_id = diagnostics.get("provider_request_id")
            if not isinstance(request_id, str):
                request_id = None
            usage = diagnostics.get("usage") if isinstance(diagnostics.get("usage"), dict) else None
        else:
            request_id = None
            usage = None
        return blocked("CONTENT_PROVIDER_ERROR_" + reason, usage, request_id)
    usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else None
    request_id = response.get("provider_request_id") if isinstance(response, dict) and isinstance(response.get("provider_request_id"), str) else None
    variants = response.get("variants") if isinstance(response, dict) else None
    if not isinstance(variants, list) or len(variants) != 3 or {x.get("angle") for x in variants if isinstance(x, dict)} != set(ANGLES):
        return blocked("CONTENT_SCHEMA_INVALID", usage, request_id)
    bodies = [x.get("body") for x in variants]
    if any(not isinstance(x, str) or not x.strip() for x in bodies) or len(set(bodies)) != 3:
        return blocked("CONTENT_NOT_DISTINCT", usage, request_id)
    allowed = {k for k, v in facts.items() if v not in (None, "")}
    for item in variants:
        if not isinstance(item.get("facts_used"), list) or not set(item["facts_used"]).issubset(allowed):
            return blocked("CONTENT_FACTS_UNGROUNDED", usage, request_id)
        if not all(isinstance(item.get(k), list) for k in ("claims", "unknowns", "warnings")):
            return blocked("CONTENT_METADATA_INVALID", usage, request_id)
    if not record_attempt("SUCCEEDED", usage, request_id):
        return {"status": "BLOCKED", "reason_codes": ["USAGE_RECEIPT_WRITE_FAILED"], "mode": GENERATIVE_MODE,
                "generation": generation, "mutated_property_store": False, "published": False, "variants": []}
    out = []
    for index, item in enumerate(sorted(variants, key=lambda x: ANGLES.index(x["angle"])), 1):
        out.append({**item, "content_variant_id": _id("content-variant", f"{seed}:{index}"), "variant_index": index,
                    "status": "DRAFT", "visibility": "INTERNAL_ONLY", "model": model,
                    "prompt_version": GENERATIVE_PROMPT_VERSION})
    return {"status": "PASS", "mode": GENERATIVE_MODE, "ai_run_id": ai_run["ai_run_id"], "facts_revision_id": ai_run["facts_revision_id"],
            "content_batch_id": _id("content-batch", seed), "content_variant_ids": [x["content_variant_id"] for x in out],
            "variants": out, "generation_id": ai_run["ai_run_id"], "generation": generation, "lineage": {**(ai_run.get("lineage") or {}), "content_batch_id": _id("content-batch", seed), "content_variant_ids": [x["content_variant_id"] for x in out]},
            "mutated_property_store": False, "published": False, "provenance_status": provenance_status, "rights_status": rights_status or "UNKNOWN"}


def build_copy_studio_preview(*, ai_run: dict[str, Any], facts: dict[str, Any], provenance_status: str, rights_status: str | None, mode: str = "APPROVAL_READY") -> dict[str, Any]:
    lineage = ai_run.get("lineage") or {}
    missing = []
    if mode != "INTERNAL_DRAFT" and provenance_status != "PASS":
        missing.append("PROVENANCE_CITATIONS_REQUIRED")
    if mode != "INTERNAL_DRAFT" and rights_status not in {"COMPANY", "PERSONAL", "APPROVED"}:
        missing.append("CONTENT_RIGHTS_REQUIRED")
    if missing:
        return {
            "status": "BLOCKED",
            "reason_codes": missing,
            "ai_run_id": ai_run.get("ai_run_id"),
            "facts_revision_id": ai_run.get("facts_revision_id"),
            "content_batch_id": None,
            "content_variant_ids": [],
            "lineage": {**lineage, "content_batch_id": None, "content_variant_ids": []},
            "variants": [],
            "created_at": _now(),
            "mutated_property_store": False,
            "published": False,
        }
    seed = str(ai_run["ai_run_id"])
    batch_id = _id("content-batch", seed)
    variants = []
    title = str(facts.get("title") or facts.get("property_type") or "Bất động sản")
    area = str(facts.get("area") or facts.get("address") or "")
    price = facts.get("price_vnd_per_month") or facts.get("price_vnd")
    price_text = f"Giá ghi nhận: {price}." if price not in (None, "") else "Giá chưa được xác nhận trong Facts."
    angle_text = {
        "NEEDS_USE": f"Bản nháp tập trung vào nhu cầu sử dụng từ dữ kiện hiện có: {area or 'khu vực chưa đủ dữ kiện'}. Không suy diễn tiện ích hoặc pháp lý.",
        "ASSET_VALUE": f"Bản nháp tập trung trình bày dữ kiện tài sản đã ghi nhận: {price_text} Không diễn giải thành cam kết sinh lời hay định giá.",
        "STORYTELLING": f"Bản nháp kể lại nguồn thông tin một cách thận trọng: {title} tại {area or 'vị trí cần xác nhận'}. Các chi tiết chưa có chứng cứ được giữ ngoài nội dung khẳng định.",
    }
    for index, style in enumerate(("NEEDS_USE", "ASSET_VALUE", "STORYTELLING"), start=1):
        variant_id = _id("content-variant", f"{seed}:{index}")
        variants.append({"content_variant_id": variant_id, "variant_index": index, "angle": style, "style": style, "title": title, "body": f"{title}. {angle_text[style]}", "status": "DRAFT", "visibility": "INTERNAL_ONLY", "created_at": _now()})
    ids = [x["content_variant_id"] for x in variants]
    return {"status": "PASS", "mode": mode, "reason_codes": [], "ai_run_id": seed, "facts_revision_id": ai_run.get("facts_revision_id"), "content_batch_id": batch_id, "content_variant_ids": ids, "lineage": {**lineage, "content_batch_id": batch_id, "content_variant_ids": ids}, "variants": variants, "provenance_status": provenance_status, "rights_status": rights_status or "UNCONFIRMED", "created_at": _now(), "mutated_property_store": False, "published": False}
