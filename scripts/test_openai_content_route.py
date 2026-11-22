"""Synthetic-only Admin route check for the opt-in staging Content action."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import admin.admin_server as admin
from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.review_center import ReviewCenter


class NoopM2:
    def audit(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        return {"status": "queued"}


class FakeProvider:
    provider_name = "TEST_ONLY"
    model = "gpt-6-luna"
    invalid = False

    def generate(self, payload):
        output = [
            {"angle": angle, "title": f"TEST_ONLY {angle}", "body": f"TEST_ONLY body {angle}",
             "facts_used": ["title"], "claims": [], "unknowns": [], "warnings": []}
            for angle in ("NEEDS_USE", "ASSET_VALUE", "STORYTELLING")
        ]
        if self.invalid:
            output[0]["facts_used"] = ["invented_field"]
        return {"variants": output, "usage": {"input_tokens": 12, "output_tokens": 24, "total_tokens": 36}, "provider_request_id": "req_TEST_ONLY"}


class FakeUsage:
    def __init__(self):
        self.rows = []

    def record(self, **kwargs):
        assert kwargs["model"] == "gpt-6-luna"
        self.rows.append(kwargs)
        return {"status": "COMPUTED_FROM_USAGE_AND_VERIFIED_RATE", "usd_cost": "0.00001320"}


def main():
    with tempfile.TemporaryDirectory(prefix="sala-content-route-") as folder:
        store = PropertyStore(Path(folder) / "store.sqlite3")
        center = ReviewCenter(store, reliability=ReliabilityBoundary(NoopM2().audit, NoopM2().enqueue))
        intake = {"id": "TEST_ONLY-intake", "source": "TEST_ONLY", "note": "TEST_ONLY synthetic apartment",
                  "preliminaryFacts": {"title": "TEST_ONLY apartment", "address": "TEST_ONLY area",
                                       "area": "TEST_ONLY district", "property_type": "APARTMENT",
                                       "transaction_type": "SALE", "price_vnd": 1000000000},
                  "missingFields": [], "conflicts": [], "rightsStatus": "UNKNOWN", "media": []}
        package = process_intake(store, center, intake)
        approved = center.approve(package["review_id"], expected_revision=1, owner_id="owner",
                                  decision="CREATE_NEW_PROPERTY", idempotency_key="TEST_ONLY-gate-a")
        property_id = approved["mutation_result"]["property_id"]
        core_before = store.get(property_id)
        response = {}
        handler = SimpleNamespace(owner_id=lambda: "owner", review_center_or_error=lambda: center,
                                  send_json=lambda value, status=200: response.update(value=value, status=status))
        usage_sink = FakeUsage()
        with patch.dict(os.environ, {"BMT_ENV": "staging"}), \
             patch.object(admin, "_CONTENT_LIVE_ENABLED", True), \
             patch.object(admin, "_AI_PROVIDER", FakeProvider()), \
             patch.object(admin, "_USAGE_LEDGER", usage_sink):
            admin.Handler.content_route_post(handler, package["review_id"], "create", {"mode": "OPENAI_LIVE"})
        value = response["value"]
        assert response["status"] == 201 and value["published"] is False
        assert value["generation_mode"] == "OPENAI_LIVE_PRIVATE_DRAFT" and len(value["variants"]) == 3
        assert value["generation"]["request_id"] == "req_TEST_ONLY"
        assert value["generation"]["usage"]["total_tokens"] == 36
        assert len(usage_sink.rows) == 1 and usage_sink.rows[0]["outcome"] == "SUCCEEDED"
        assert store.get(property_id) == core_before
        assert center.packages[package["review_id"]]["content_batches"][-1]["status"] == "PRIVATE_DRAFT"

        from property_memory.content_studio import build_generative_internal_draft
        bad_sink = FakeUsage()
        bad_provider = FakeProvider()
        bad_provider.invalid = True
        bad = build_generative_internal_draft(ai_run={"ai_run_id":"TEST_ONLY-bad", "facts_revision_id":"facts-test-r1"},
                                               facts=package["candidate"]["facts"], provenance_status="PASS",
                                               rights_status="APPROVED", provider=bad_provider, model="gpt-6-luna", usage_sink=bad_sink)
        assert bad["status"] == "BLOCKED" and bad["generation"]["request_id"] == "req_TEST_ONLY"
        assert len(bad_sink.rows) == 1 and bad_sink.rows[0]["outcome"] == "FAILED"
        from property_memory.providers.openai_provider import ProviderError

        class FailingProvider:
            provider_name = "TEST_ONLY"

            def generate(self, payload):
                raise ProviderError("PROVIDER_4XX", "TEST_ONLY provider failure",
                                    diagnostics={"http_status": 400, "error_code": "model_not_found"})

        safe_failure = build_generative_internal_draft(
            ai_run={"ai_run_id": "TEST_ONLY-fail", "facts_revision_id": "facts-test-r1"},
            facts=package["candidate"]["facts"], provenance_status="PASS", rights_status="APPROVED",
            provider=FailingProvider(), model="gpt-6-luna", usage_sink=FakeUsage())
        assert safe_failure["reason_codes"] == ["CONTENT_PROVIDER_ERROR_PROVIDER_4XX"]
        assert safe_failure["generation"]["provider_http_status"] == 400
        assert safe_failure["generation"]["provider_error_code"] == "model_not_found"
        store.close()
        print("PASS: TEST_ONLY Admin AI route, Gate A revision binding, exactly 3 variants, usage/request receipt, private-only state, Property Core unchanged, sanitized provider failure receipt")


if __name__ == "__main__":
    main()
