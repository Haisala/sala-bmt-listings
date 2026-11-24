"""Redacted, durable provider usage ledger with fail-closed pricing."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int | None]:
    u = usage if isinstance(usage, dict) else {}
    details = u.get("input_tokens_details") if isinstance(u.get("input_tokens_details"), dict) else {}
    out_details = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
    def integer(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None
    return {
        "input_tokens": integer(u.get("input_tokens")),
        "output_tokens": integer(u.get("output_tokens")),
        "total_tokens": integer(u.get("total_tokens")),
        "cached_read_tokens": integer(details.get("cached_tokens")),
        "cache_write_tokens": integer(details.get("cache_write_tokens")),
        "reasoning_tokens": integer(out_details.get("reasoning_tokens")),
        "media_units": integer(u.get("media_units")),
        "tool_units": integer(u.get("tool_units")),
    }


def compute_usd(usage: dict[str, Any], pricing: dict[str, Any] | None) -> dict[str, Any]:
    tokens = normalize_usage(usage)
    if tokens["cache_write_tokens"] is not None and tokens["cache_write_tokens"] > 0 and (not isinstance(pricing, dict) or pricing.get("cache_write_semantics") != "VERIFIED"):
        return {"usd_cost": None, "status": "PRICE_OR_USAGE_SEMANTICS_UNVERIFIED", "components": {}, "tokens": tokens, "reason": "cache_write_category_not_proven_native_or_billable_for_this_request"}
    if not isinstance(pricing, dict) or pricing.get("status") != "VERIFIED":
        return {"usd_cost": None, "status": "PRICE_NOT_VERIFIED", "components": {}, "tokens": tokens}
    if tokens["input_tokens"] is None or tokens["output_tokens"] is None:
        return {"usd_cost": None, "status": "USAGE_INCOMPLETE", "components": {}, "tokens": tokens}
    # Reasoning tokens are a subset of output tokens; never charge them separately.
    rates = {k: Decimal(str(pricing.get(k) if pricing.get(k) is not None else 0)) for k in ("input_per_token_usd", "output_per_token_usd", "cached_read_per_token_usd", "cache_write_per_token_usd")}
    components = {"input": Decimal(tokens["input_tokens"] or 0) * rates["input_per_token_usd"], "output": Decimal(tokens["output_tokens"] or 0) * rates["output_per_token_usd"], "cached_read": Decimal(tokens["cached_read_tokens"] or 0) * rates["cached_read_per_token_usd"], "cache_write": Decimal(tokens["cache_write_tokens"] or 0) * rates["cache_write_per_token_usd"]}
    quant = Decimal("0.00000001")
    return {"usd_cost": str(sum(components.values()).quantize(quant, rounding=ROUND_HALF_UP)), "status": "COMPUTED_FROM_USAGE_AND_VERIFIED_RATE", "components": {k: str(v.quantize(quant, rounding=ROUND_HALF_UP)) for k, v in components.items()}, "tokens": tokens, "billing_reconciled_usd": None}


def official_pricing_catalog() -> dict[str, dict[str, Any]]:
    """Rates captured from the official pricing page; billing remains unreconciled."""
    return {"gpt-6-luna": {"status": "VERIFIED", "cache_write_semantics": "UNKNOWN", "source_url": "https://platform.openai.com/pricing", "effective_date": "2026-11-17", "currency": "USD", "unit": "per token (converted from per 1M tokens)", "input_per_token_usd": "0.00000010", "output_per_token_usd": "0.00000050", "cached_read_per_token_usd": "0.00000001", "cache_write_per_token_usd": None}}


class UsageLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS provider_usage (
            receipt_id TEXT PRIMARY KEY, intake_id TEXT, review_id TEXT, ai_run_id TEXT,
            stage TEXT NOT NULL, operation TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            provider_request_id TEXT, attempted_at TEXT NOT NULL, outcome TEXT NOT NULL,
            retry_index INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT,
            usage_json TEXT NOT NULL, cost_json TEXT NOT NULL, provenance TEXT NOT NULL,
            UNIQUE(provider_request_id), UNIQUE(idempotency_key, retry_index)
        )""")
        self.connection.commit()

    def record(self, *, receipt_id: str, intake_id: str | None, stage: str, operation: str,
               provider: str, model: str, outcome: str, usage: dict[str, Any] | None,
               provider_request_id: str | None = None, retry_index: int = 0,
               idempotency_key: str | None = None, review_id: str | None = None,
               ai_run_id: str | None = None, pricing: dict[str, Any] | None = None) -> dict[str, Any]:
        cost = compute_usd(usage or {}, pricing)
        row = (receipt_id, intake_id, review_id, ai_run_id, stage, operation, provider, model,
               provider_request_id, _now(), outcome, int(retry_index), idempotency_key,
               json.dumps(cost["tokens"], sort_keys=True), json.dumps(cost, sort_keys=True),
               "PROVIDER_RETURNED_USAGE" if usage else "USAGE_UNAVAILABLE")
        with self.connection:
            self.connection.execute("""INSERT OR IGNORE INTO provider_usage
                (receipt_id,intake_id,review_id,ai_run_id,stage,operation,provider,model,provider_request_id,attempted_at,outcome,retry_index,idempotency_key,usage_json,cost_json,provenance)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
        return {"receipt_id": receipt_id, "tokens": cost["tokens"], "usd_cost": cost["usd_cost"], "status": cost["status"]}

    def report(self, *, review_id: str, intake_id: str, historical_usage: dict[str, Any] | None = None, model: str = "gpt-6-luna", provider_total_usd: str | None = None, provider_total_source: str | None = None, provider_total_scope: str | None = None, provider_total_as_of: str | None = None) -> dict[str, Any]:
        rows = self.connection.execute("SELECT receipt_id,stage,operation,provider,model,provider_request_id,attempted_at,outcome,retry_index,usage_json,cost_json,provenance FROM provider_usage WHERE intake_id=? ORDER BY attempted_at", (intake_id,)).fetchall()
        items = [dict(zip(("receipt_id","stage","operation","provider","model","provider_request_id","attempted_at","outcome","retry_index","usage","cost","provenance"), r)) for r in rows]
        for item in items:
            item["usage"] = json.loads(item["usage"]); item["cost"] = json.loads(item["cost"]); item["provider_request_id"] = (item["provider_request_id"] or "")[:8] + "…" if item["provider_request_id"] else None
        if historical_usage is not None and not items:
            pricing = official_pricing_catalog().get(model)
            computed = compute_usd(historical_usage, pricing)
            items.append({"receipt_id":"historical-fixture", "stage":"intake_extraction", "operation":"compatibility_projection", "provider":"openai-responses", "model":model, "provider_request_id":None, "attempted_at":None, "outcome":"HISTORICAL_FIXTURE", "retry_index":0, "usage":computed["tokens"], "cost":computed, "provenance":"HISTORICAL_READ_ONLY_FIXTURE"})
        detail_complete = bool(items) and all(x["cost"].get("usd_cost") is not None for x in items)
        if provider_total_usd is not None and provider_total_source and provider_total_scope:
            total_usd, total_status, total_source = str(Decimal(provider_total_usd).quantize(Decimal("0.00000001"))), "TOTAL_PROVIDER_REPORTED", provider_total_source
        elif detail_complete:
            total = sum((Decimal(str(x["cost"]["usd_cost"])) for x in items), Decimal("0"))
            total_usd, total_status, total_source = str(total.quantize(Decimal("0.00000001"))), "TOTAL_ESTIMATED", "LEDGER_COMPONENTS"
        else:
            total_usd, total_status, total_source = None, "TOTAL_UNKNOWN", "NONE"
        return {"review_id": review_id, "intake_id": intake_id, "report_status": "PASS_OFFLINE", "items": items, "total_tokens": sum((x["usage"].get("total_tokens") or 0) for x in items), "total_usd": total_usd, "total_usd_status": total_status, "total_usd_source": total_source, "total_usd_scope": provider_total_scope if provider_total_usd is not None else "review", "total_usd_as_of": provider_total_as_of, "detail_usd_breakdown": [{"stage": x["stage"], "usd_cost": x["cost"].get("usd_cost"), "status": x["cost"].get("status"), "provenance": x["provenance"]} for x in items], "detail_reconciliation": "COMPLETE" if detail_complete else "INCOMPLETE", "billing_reconciled_usd": None, "billing_status": "NOT_RECONCILED", "pricing_catalog": official_pricing_catalog().get(model)}
