"""Synthetic-only contract checks for staging AI Content generation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from property_memory.providers.openai_provider import (
    MAX_CONTENT_REQUEST_BYTES,
    MAX_CONTENT_INPUT_TOKENS,
    OpenAIPropertyProvider,
    ProviderError,
    _content_cost_upper_bound,
    build_content_request,
)


class Transport:
    def __init__(self, variants):
        self.variants = variants
        self.calls = 0

    def send(self, payload, api_key, *, timeout):
        self.calls += 1
        assert api_key == "TEST_ONLY_KEY"
        assert payload["model"] == "gpt-6-luna"
        assert payload["max_output_tokens"] == 900
        return {"id": "req_TEST_ONLY", "usage": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
                "output_json": {"variants": self.variants}}


def variants():
    return [
        {"angle": angle, "title": f"TEST_ONLY {angle}", "body": f"TEST_ONLY body {angle}",
         "facts_used": ["title"], "claims": [], "unknowns": [], "warnings": []}
        for angle in ("NEEDS_USE", "ASSET_VALUE", "STORYTELLING")
    ]


def main():
    realistic_facts = {
        "title": "TEST_ONLY Synthetic Home", "property_type": "HOUSE",
        "address": "TEST_ONLY fabricated address", "area": "72.5 m²",
        "width": 5, "length": 14.5, "land_area": 72.5, "price_type": "SALE",
        "price_vnd": 2500000000, "legal_status": "CERTIFICATE",
        "road_access": "TEST_ONLY road", "house_type": "TEST_ONLY house",
        "bedrooms": 2, "bathrooms": 1,
        "description": "TEST_ONLY " + "synthetic factual detail " * 20,
        "owner_contact": "TEST_ONLY_PRIVATE_MUST_NOT_BE_SENT",
    }
    request = build_content_request({"generation_id": "ai-run-" + "a" * 24,
                                    "facts_revision_id": "facts-property-" + "b" * 24 + "-r1",
                                    "facts": realistic_facts}, "gpt-6-luna")
    request_input = json.loads(request["input"])
    sent_facts = request_input["facts"]
    assert "generation_id" not in request_input and "facts_revision_id" not in request_input
    assert sent_facts and "owner_contact" not in sent_facts
    assert {"title", "property_type", "address", "area", "price_vnd", "price_type", "legal_status"} <= set(sent_facts)
    assert set(sent_facts).issubset({
        "title", "property_type", "address", "area", "price_vnd", "price_vnd_per_month",
        "price", "price_type", "legal_status", "house_type", "bedrooms", "bathrooms",
        "road_access", "features", "description",
    })
    assert len(sent_facts.get("description", "")) <= 160
    size = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert request["text"]["format"]["schema"]["properties"]["variants"]["maxItems"] == 3
    input_upper_bound = (len(request["instructions"].encode()) + len(request["input"].encode())
                         + len(json.dumps(request["text"]["format"]["schema"], separators=(",", ":")).encode())
                         + len("content_studio_three"))
    assert input_upper_bound <= MAX_CONTENT_INPUT_TOKENS
    assert size < MAX_CONTENT_REQUEST_BYTES
    assert _content_cost_upper_bound(size, 900) < 0.01
    truncated = build_content_request({"facts": {"description": "TEST_ONLY " + "x" * 9000}}, "gpt-6-luna")
    assert len(json.loads(truncated["input"])["facts"]["description"]) == 160
    try:
        build_content_request({"facts": {}}, "unexpected-model")
        raise AssertionError("unapproved model was accepted")
    except ValueError as exc:
        assert str(exc) == "CONTENT_MODEL_OR_OUTPUT_CAP_INVALID"

    transport = Transport(variants())
    provider = OpenAIPropertyProvider(api_key="TEST_ONLY_KEY", transport=transport)
    result = provider.generate({"generation_id": "TEST_ONLY", "facts_revision_id": "facts-test-r1",
                                "facts": {"title": "TEST_ONLY"}, "output_schema": "exactly_three_variants"})
    assert len(result["variants"]) == 3 and result["provider_request_id"] == "req_TEST_ONLY"
    assert result["usage"]["total_tokens"] == 50 and transport.calls == 1

    bad_transport = Transport(variants() + [variants()[0], variants()[1]])
    try:
        OpenAIPropertyProvider(api_key="TEST_ONLY_KEY", transport=bad_transport).generate({"facts": {"title": "TEST_ONLY"}})
        raise AssertionError("five-variant response was accepted")
    except ProviderError as exc:
        assert exc.code == "MALFORMED_CONTENT_OUTPUT"
    assert bad_transport.calls == 1

    class MalformedResponseTransport:
        def send(self, payload, api_key, *, timeout):
            return {"id": "req_TEST_ONLY_malformed", "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 71, "output_tokens": 900, "total_tokens": 971},
                    "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "PRIVATE_MUST_NOT_LEAK"}]}]}

    try:
        OpenAIPropertyProvider(api_key="TEST_ONLY_KEY", transport=MalformedResponseTransport()).generate({"facts": {"title": "TEST_ONLY"}})
        raise AssertionError("malformed response was accepted")
    except ProviderError as exc:
        assert exc.code == "MALFORMED_STRUCTURED_OUTPUT"
        assert exc.diagnostics["provider_request_id"] == "req_TEST_ONLY_malformed"
        assert exc.diagnostics["response_status"] == "incomplete"
        assert exc.diagnostics["incomplete_reason"] == "max_output_tokens"
        assert exc.diagnostics["usage"]["total_tokens"] == 971
        assert exc.diagnostics["output_item_types"] == ["reasoning"]
        assert "PRIVATE_MUST_NOT_LEAK" not in json.dumps(exc.diagnostics)
    print("PASS: TEST_ONLY 3-variant schema, 900 output cap, bounded input, pre-call $0.01 ceiling, model allowlist, usage/request receipt")


if __name__ == "__main__":
    main()
