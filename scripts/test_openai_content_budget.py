"""TEST_ONLY provider contract for bounded one-request/five-content generation."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from property_memory.providers.openai_provider import (
    MAX_CONTENT_REQUEST_BYTES, MAX_CONTENT_INPUT_TOKENS, CONTENT_VARIANT_COUNT,
    OpenAIPropertyProvider, ProviderError, _content_cost_upper_bound, build_content_request,
)


def compact_variants():
    return [{"i": i, "t": f"TEST_ONLY title {i}", "b": f"TEST_ONLY body {i}",
             "f": ["title"], "u": [], "w": []} for i in range(1, 6)]


class Transport:
    def __init__(self, value): self.value, self.calls = value, 0
    def send(self, payload, api_key, *, timeout):
        self.calls += 1
        assert api_key == "TEST_ONLY_KEY"
        assert payload["model"] == "gpt-6-luna" and payload["max_output_tokens"] == 900
        return {"id":"req_TEST_ONLY", "usage":{"input_tokens":20,"output_tokens":30,"total_tokens":50}, "output_json":self.value}


def main():
    facts={"title":"TEST_ONLY Synthetic Home","property_type":"HOUSE","address":"TEST_ONLY fabricated address",
           "area":"72.5 m²","price_type":"SALE","price_vnd":2500000000,"legal_status":"CERTIFICATE",
           "description":"TEST_ONLY "+"synthetic detail "*20,"owner_contact":"TEST_ONLY_PRIVATE"}
    request=build_content_request({"facts":facts},"gpt-6-luna")
    sent=json.loads(request["input"])["facts"]
    assert "owner_contact" not in sent and sent and set(sent)<=set(facts)-{"owner_contact"}
    schema=request["text"]["format"]["schema"]
    assert schema["properties"]["v"]["minItems"]==CONTENT_VARIANT_COUNT==5
    assert schema["properties"]["v"]["maxItems"]==5
    size=len(json.dumps(request,ensure_ascii=False,separators=(",",":" )).encode())
    upper=(len(request["instructions"].encode())+len(request["input"].encode())+
           len(json.dumps(schema,ensure_ascii=False,separators=(",",":" )).encode())+len("content_five"))
    assert upper<=MAX_CONTENT_INPUT_TOKENS and size<MAX_CONTENT_REQUEST_BYTES
    assert request["max_output_tokens"]==900 and _content_cost_upper_bound(size,900)<0.01
    fact_enum=schema["properties"]["v"]["items"]["properties"]["f"]["items"]["enum"]
    assert set(fact_enum)==set(sent) and "owner_contact" not in fact_enum
    assert schema["properties"]["f"]["maxItems"]==0
    assert "top-level f must be empty" in request["instructions"]
    try: build_content_request({"facts":{},"source_text":""},"gpt-6-luna")
    except ValueError as e: assert str(e)=="CONTENT_INPUT_REQUIRED"
    else: raise AssertionError("empty prompt accepted")
    try: build_content_request({"facts":{"title":"x"},"source_text":"x"*321},"gpt-6-luna")
    except ValueError as e: assert str(e)=="CONTENT_SOURCE_LIMIT_INVALID"
    else: raise AssertionError("silently truncated source")

    value={"v":compact_variants(),"f":[],"m":[],"c":[]}
    transport=Transport(value)
    result=OpenAIPropertyProvider(api_key="TEST_ONLY_KEY",transport=transport).generate({"facts":{"title":"TEST_ONLY"}})
    assert len(result["variants"])==5 and result["provider_request_id"]=="req_TEST_ONLY"
    assert result["input_fact_keys"]==["title"]
    assert result["usage"]["total_tokens"]==50 and transport.calls==1
    assert result["normalized_facts"]==[]
    for malformed in (value|{"v":compact_variants()[:4]}, value|{"v":compact_variants()+[compact_variants()[0]]}):
        t=Transport(malformed)
        try: OpenAIPropertyProvider(api_key="TEST_ONLY_KEY",transport=t).generate({"facts":{"title":"TEST_ONLY"}})
        except ProviderError as e: assert e.code=="MALFORMED_CONTENT_OUTPUT"
        else: raise AssertionError("non-five response accepted")
        assert t.calls==1
    print("PASS: TEST_ONLY one request, exact 5-output contract, 1,200 input bound, 900 output cap, pre-call $0.01 ceiling, no source truncation")

if __name__=="__main__": main()
