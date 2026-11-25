"""TEST_ONLY one-touch Intake -> Review -> five private Content contract."""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import admin.admin_server as admin
from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.review_center import ReviewCenter

DEFAULT_FACTS_USED=object()
DEFAULT_USAGE=object()
DEFAULT_INPUT_KEYS=object()

class Noop:
    def audit(self,*a,**k): pass
    def enqueue(self,*a,**k): return {"status":"queued"}
class Provider:
    provider_name="TEST_ONLY_MOCK";model="gpt-6-luna"
    def __init__(self,count=5,facts_used=DEFAULT_FACTS_USED,normalized_facts=None,usage=DEFAULT_USAGE,input_fact_keys=DEFAULT_INPUT_KEYS):
        self.calls=0;self.count=count;self.facts_used=facts_used;self.normalized_facts=normalized_facts or [];self.usage=usage;self.input_fact_keys=input_fact_keys
    def generate(self,payload):
        self.calls+=1
        assert payload["output_count"]==5 and payload["media_analysis"]=="NOT_SENT"
        used=["title"] if self.facts_used is DEFAULT_FACTS_USED else self.facts_used
        usage={"input_tokens":100,"output_tokens":300,"total_tokens":400} if self.usage is DEFAULT_USAGE else self.usage
        result={"variants":[{"variant_index":i,"title":f"Fixture {i}","body":f"Distinct synthetic draft {i}","facts_used":used,"citations_used":[],"unknowns":[],"warnings":[]} for i in range(1,self.count+1)],
                "normalized_facts":self.normalized_facts,"missing_fields":["area"],"conflicts":[],
                "usage":usage,"provider_request_id":"req_TEST_ONLY"}
        if self.input_fact_keys is not DEFAULT_INPUT_KEYS:result["input_fact_keys"]=self.input_fact_keys
        return result
class Usage:
    def __init__(self):self.rows=[]
    def record(self,**kw):
        self.rows.append(kw);return {"status":"ESTIMATE_ONLY","usd_cost":"0.0001","tokens":kw["usage"]}

def call(handler,intake_id,key,payload=None):
    out={};handler.send_json=lambda value,status=200:out.update(value=value,status=status)
    admin.Handler.create_content_from_intake(handler,intake_id,{"idempotency_key":key,**(payload or {})});return out

def main():
  with tempfile.TemporaryDirectory(prefix="sala-one-touch-test-") as folder:
    root=Path(folder); private=root/"private";private.mkdir(mode=0o700)
    inbox=private/"inbox";inbox.mkdir(mode=0o700)
    with patch.object(admin,"PRIVATE_ROOT",private),patch.object(admin,"INBOX_ROOT",inbox),patch.object(admin,"INDEX_PATH",private/"index.json"):
      saved,replay,_=admin.create_intake_record({"source":"Cá nhân","note":"Giá thuê: 6 triệu/tháng. Căn hộ mặt tiền Tôn Đức Thắng","evidence_source_id":"TEST_ONLY-source","evidence_claim":"TEST_ONLY source claim","evidence_rights":"PERSONAL"},[],supplied_key="TEST_ONLY-intake-evidence-save-001")
      assert saved["preliminaryFacts"]["price_vnd_per_month"]==6000000 and saved["preliminaryFacts"]["price_cycle"]=="MONTH"
      assert saved["ownerEvidence"]["source_id"].startswith("intake:") and saved["ownerEvidence"]["rights_status"]=="PERSONAL"
      assert "Giá thuê" in saved["ownerEvidence"]["claim"]
      replayed,is_replay,_=admin.create_intake_record({"source":"Cá nhân","note":"different replay body","evidence_source_id":"TEST_ONLY-other","evidence_claim":"TEST_ONLY other","evidence_rights":"COMPANY"},[],supplied_key="TEST_ONLY-intake-evidence-save-001")
      assert is_replay and replayed["id"]==saved["id"] and replayed["ownerEvidence"]==saved["ownerEvidence"]
    store=PropertyStore(private/"store.sqlite3"); noop=Noop()
    center=ReviewCenter(store,reliability=ReliabilityBoundary(noop.audit,noop.enqueue))
    intake={"id":"TEST_ONLY-intake-001","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-fixture",
      "note":"TEST_ONLY synthetic 72 square meter home in TEST_ONLY district",
      "preliminaryFacts":{"title":"TEST_ONLY-home","property_type":"HOUSE","transaction_type":"SALE","price_vnd":1000000000,"address":"TEST_ONLY address","area":"72 m²","house_type":"TEST_ONLY house","bedrooms":2,"bathrooms":1},
      "ownerEvidence":{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture","rights_status":"PERSONAL"},"media":[]}
    pkg=process_intake(store,center,intake,ai_gateway=None,operation_id=intake["id"])
    pkg=center.packages[pkg["review_id"]]
    pkg["evidence_revisions"]=[{"evidence_revision_id":"TEST_ONLY-evidence-r1","evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture","rights_status":"PERSONAL"}]}}]
    center._persist(pkg)
    missing={"id":"TEST_ONLY-intake-no-rights","source":"TEST_ONLY","note":"TEST_ONLY synthetic source with title: TEST_ONLY rental home","preliminaryFacts":{"title":"TEST_ONLY rental home","property_type":"APARTMENT","transaction_type":"RENT","price_vnd_per_month":6000000,"price_cycle":"MONTH","address":"TEST_ONLY address","area":"72 m²"},"media":[]}
    provider=Provider();usage=Usage()
    handler=SimpleNamespace(owner_id=lambda:"owner",review_center_or_error=lambda:center)
    records=[intake,missing]
    with patch.dict(os.environ,{"BMT_ENV":"test","BMT_TEST_ONLY":"1","SALA_E2E_ROOT":folder,"BMT_ADMIN_OWNER_ID":"owner"}), \
         patch.object(admin,"PRIVATE_ROOT",private),patch.object(admin,"HOST","127.0.0.1"), \
         patch.object(admin,"_CONTENT_LIVE_ENABLED",True),patch.object(admin,"_AI_PROVIDER",provider),patch.object(admin,"_USAGE_LEDGER",usage), \
         patch.object(admin,"load_index",lambda:records),patch.object(admin,"save_index",lambda items:None):
      result=call(handler,intake["id"],"TEST_ONLY-idempotency-key-001")
      assert result["status"]==201,result
      body=result["value"]
      assert len(body["variants"])==5 and body["published"] is False and body["mutated_property_store"] is False
      assert intake["status"]=="CONTENT_CREATED" and intake["contentReviewId"]==body["review_id"]
      assert body["media_analysis"]=="NOT_SENT" and provider.calls==1 and len(usage.rows)==1
      assert body["generation"]["content_variant_ids"]==[v["content_variant_id"] for v in body["variants"]]
      assert all(v["intake_id"]==intake["id"] and v["review_id"]==body["review_id"]
                 and v["review_revision"]==body["review_revision"]
                 and v["facts_revision_id"]==body["facts_revision_id"] for v in body["variants"])
      assert body["generation"]["estimated_cost_usd"]=="0.0001"
      assert len(usage.rows[0]["usage"])==3 and usage.rows[0]["review_id"]==pkg["review_id"]
      denied_rights=call(handler,missing["id"],"TEST_ONLY-no-rights-key-001")
      assert denied_rights["status"]==409 and denied_rights["value"]["missing_evidence"]==["rights_status"]
      assert denied_rights["value"]["provider_calls"]==0 and denied_rights["value"]["intake_id"]==missing["id"]
      assert provider.calls==1 and len(usage.rows)==1
      again=call(handler,intake["id"],"TEST_ONLY-idempotency-key-001")
      assert again["status"]==200 and len(again["value"]["variants"])==5 and provider.calls==1 and len(usage.rows)==1
      persisted=center.packages[pkg["review_id"]]
      assert len(persisted["content_batches"])==1 and len(persisted["content_batches"][0]["variants"])==5
      assert persisted["content_batches"][0]["status"]=="PRIVATE_DRAFT"
      assert store.list_properties()==[]
      denied=SimpleNamespace(owner_id=lambda:None,review_center_or_error=lambda:center,send_json=lambda v,status=200:None)
      denied_out=call(denied,intake["id"],"TEST_ONLY-anonymous-key-001")
      assert denied_out["status"]==403 and provider.calls==1
      missing["ownerEvidence"]={"source_id":"TEST_ONLY-owner-source","claim":"TEST_ONLY owner-confirmed synthetic source","rights_status":"PERSONAL"}
      generated=call(handler,missing["id"],"TEST_ONLY-evidence-idempotency-001")
      assert generated["status"]==201 and len(generated["value"]["variants"])==5 and provider.calls==2,generated
      generated_review=center.packages[generated["value"]["review_id"]]
      assert len(generated_review["evidence_revisions"])==1
      assert generated_review["evidence_revisions"][0]["origin"]=="OWNER_CONFIRMED"
      conflict=call(handler,missing["id"],"TEST_ONLY-evidence-conflict-001",{"evidence":{"rights_status":"COMPANY","citations":[{"source_id":"TEST_ONLY-other","claim":"TEST_ONLY different source","rights_status":"COMPANY"}]}})
      assert conflict["status"]==409 and conflict["value"]["error"]=="EVIDENCE_INTAKE_MISMATCH" and provider.calls==2
      intake_b={"id":"TEST_ONLY-intake-b","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-fixture-b","note":"TEST_ONLY separate source","preliminaryFacts":dict(intake["preliminaryFacts"]),"ownerEvidence":{"source_id":"TEST_ONLY-source-b","claim":"TEST_ONLY evidence for B","rights_status":"COMPANY"},"media":[]}
      records.append(intake_b)
      wrong_evidence=call(handler,intake_b["id"],"TEST_ONLY-cross-intake-001",{"evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture","rights_status":"PERSONAL"}]}})
      assert wrong_evidence["status"]==409 and wrong_evidence["value"]["error"]=="EVIDENCE_INTAKE_MISMATCH" and wrong_evidence["value"]["intake_id"]==intake_b["id"]
      failed_intake={"id":"TEST_ONLY-intake-bad-facts-used","source":"TEST_ONLY","sourceGroup":"SYNTHETIC",
        "note":"TEST_ONLY source text","preliminaryFacts":{"title":"TEST_ONLY","property_type":"APARTMENT",
        "transaction_type":"SALE","price_vnd":1000000,"address":"TEST_ONLY Lane","area":"50 m2"},
        "ownerEvidence":{"source_id":"TEST_ONLY-source-c","claim":"TEST_ONLY only","rights_status":"COMPANY"},"media":[]}
      records.append(failed_intake);bad_provider=Provider(facts_used=["TEST_ONLY"])
      with patch.object(admin,"_AI_PROVIDER",bad_provider):
        bad_result=call(handler,failed_intake["id"],"TEST_ONLY-invalid-facts-used-001")
      receipt=bad_result["value"].get("receipt",{})
      assert bad_result["status"]==422 and bad_result["value"]["reason_codes"]==["CONTENT_FACTS_UNGROUNDED"]
      assert bad_result["value"]["validation_diagnostic"]=={
        "stage":"variant_facts_used","rule":"unique_submitted_fact_keys_only","variant_index":1,
        "unknown_key_count":1,"duplicate_key_count":0,"value_type_invalid":False}
      assert "TEST_ONLY" not in json.dumps(bad_result["value"]["validation_diagnostic"])
      assert receipt.get("request_id")=="req_TEST_ONLY" and receipt.get("model")=="gpt-6-luna"
      assert all(receipt.get("usage",{}).get(k)==v for k,v in {"input_tokens":100,"output_tokens":300,"total_tokens":400}.items())
      assert receipt.get("estimated_cost_usd")=="0.0001" and receipt.get("ledger_status")=="RECORDED"
      assert bad_provider.calls==1 and store.list_properties()==[] and bad_result["value"]["published"] is False
    # Exercise the actual authenticated POST route over loopback with TEST_ONLY storage.
    import threading, urllib.error, urllib.request
    from http.server import ThreadingHTTPServer
    http_root=root/"http"; http_root.mkdir(mode=0o700)
    http_private=http_root/"private"; http_private.mkdir(mode=0o700)
    http_store=PropertyStore(http_private/"store.sqlite3")
    http_center=ReviewCenter(http_store,reliability=ReliabilityBoundary(noop.audit,noop.enqueue))
    http_intake={"id":"TEST_ONLY-intake-http-001","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-http-fixture",
      "note":"TEST_ONLY synthetic source text","preliminaryFacts":{"property_type":"APARTMENT","transaction_type":"SALE","price_vnd":1000000000,"address":"TEST_ONLY address","area":"70 m²"},
      "ownerEvidence":{"source_id":"TEST_ONLY-http-source","claim":"TEST_ONLY source","rights_status":"PERSONAL"},"inputRevision":1,"media":[]}
    (http_private/"index.json").write_text(json.dumps([http_intake]))
    http_provider=Provider();http_usage=Usage();sid="TEST_ONLY-session"
    env={"BMT_ENV":"test","BMT_TEST_ONLY":"1","SALA_E2E_ROOT":str(http_root),"BMT_ADMIN_OWNER_ID":"owner",
      "BMT_ADMIN_LOCAL_HTTP_DEV":"1","BMT_ADMIN_PUBLIC_ORIGIN":"","BMT_ADMIN_ALLOWED_IP":"","BMT_ADMIN_SECURE_COOKIE":"0"}
    with patch.dict(os.environ,env,clear=True),patch.object(admin,"PRIVATE_ROOT",http_private),patch.object(admin,"INBOX_ROOT",http_private/"inbox"),\
         patch.object(admin,"INDEX_PATH",http_private/"index.json"),patch.object(admin,"HOST","127.0.0.1"),patch.object(admin,"ALLOWED_REMOTE_IP",""),\
         patch.object(admin,"SECURE_COOKIE",False),patch.object(admin,"REVIEW_CENTER",http_center),patch.object(admin,"_CONTENT_LIVE_ENABLED",True),\
         patch.object(admin,"_AI_PROVIDER",http_provider),patch.object(admin,"_USAGE_LEDGER",http_usage):
      admin.SESSIONS[sid]=9999999999;admin.SESSION_OWNERS[sid]="owner"
      server=ThreadingHTTPServer(("127.0.0.1",0),admin.Handler);admin.PORT=server.server_port
      thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
      url=f"http://127.0.0.1:{server.server_port}/api/intakes/{http_intake['id']}/create-content"
      request=urllib.request.Request(url,data=json.dumps({"idempotency_key":"TEST_ONLY-http-idempotency-001"}).encode(),method="POST",
        headers={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}","Cookie":"bmt_admin_session="+sid})
      try: urllib.request.urlopen(request,timeout=5)
      except urllib.error.HTTPError as e: status=e.code;missing_body=json.loads(e.read())
      else: raise AssertionError("incomplete source unexpectedly generated Content")
      assert status==409 and missing_body["missing_fields"]==["title"] and missing_body["provider_calls"]==0
      assert http_provider.calls==0 and http_center.packages=={}
      draft_url=f"http://127.0.0.1:{server.server_port}/api/intakes/{http_intake['id']}/draft"
      draft_req=urllib.request.Request(draft_url,data=json.dumps({"expected_input_revision":1,"facts":{"title":"TEST_ONLY HTTP property"}}).encode(),method="POST",
        headers={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}","Cookie":"bmt_admin_session="+sid})
      with urllib.request.urlopen(draft_req,timeout=5) as response: draft_status=response.status;draft_body=json.loads(response.read())
      assert draft_status==201 and draft_body["input_revision"]==2
      assert http_intake["id"]==draft_body["id"] and http_store.list_properties()==[]
      with urllib.request.urlopen(request,timeout=5) as response: status=response.status;http_body=json.loads(response.read())
      assert status==201 and len(http_body["variants"])==5 and http_body["published"] is False
      with urllib.request.urlopen(request,timeout=5) as response: replay_status=response.status;replay_body=json.loads(response.read())
      assert replay_status==200 and replay_body["content_batch_id"]==http_body["content_batch_id"]
      assert http_provider.calls==1 and len(http_usage.rows)==1 and http_store.list_properties()==[]
      http_intake_b={"id":"TEST_ONLY-intake-http-b","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-http-fixture-b","note":"TEST_ONLY separate source",
        "preliminaryFacts":{"title":"TEST_ONLY B","property_type":"APARTMENT","transaction_type":"SALE","price_vnd":1000000000,"address":"TEST_ONLY B address","area":"70 m²"},
        "ownerEvidence":{"source_id":"TEST_ONLY-http-source-b","claim":"TEST_ONLY source B","rights_status":"COMPANY"},"media":[]}
      admin.save_index(admin.load_index()+[http_intake_b])
      other_evidence={"evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-http-source","claim":"TEST_ONLY source","rights_status":"PERSONAL"}]}}
      cross_url=f"http://127.0.0.1:{server.server_port}/api/intakes/{http_intake_b['id']}/create-content"
      cross_req=urllib.request.Request(cross_url,data=json.dumps({"idempotency_key":"TEST_ONLY-cross-intake-http-001",**other_evidence}).encode(),method="POST",
        headers={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}","Cookie":"bmt_admin_session="+sid})
      try: urllib.request.urlopen(cross_req,timeout=5)
      except urllib.error.HTTPError as e: cross_status=e.code;cross_body=json.loads(e.read())
      else: raise AssertionError("evidence from intake A was accepted for intake B")
      assert cross_status==409 and cross_body["error"]=="EVIDENCE_INTAKE_MISMATCH" and cross_body["intake_id"]==http_intake_b["id"]
      assert http_provider.calls==1 and len(http_usage.rows)==1
      server.shutdown();server.server_close();thread.join(timeout=3);admin.SESSIONS.pop(sid,None);admin.SESSION_OWNERS.pop(sid,None);http_store.close()

    from property_memory.content_studio import build_generative_content_batch
    from property_memory.providers.openai_provider import build_content_request
    from property_memory.usage_ledger import compute_usd, official_pricing_catalog
    base=dict(generation_id="TEST_ONLY-malformed",facts={"title":"TEST_ONLY"},provenance={"intake_id":intake["id"]},
      rights_status="PERSONAL",media=[],model="gpt-6-luna",source_text="TEST_ONLY text")
    valid=Provider();valid_usage=Usage();out=build_generative_content_batch(**base,provider=valid,usage_sink=valid_usage)
    assert out["status"]=="PASS" and len(out["variants"])==5 and valid.calls==1
    invalid_shapes=(["unknown_key"], ["TEST_ONLY"], None, "title", [1], ["title","title"])
    for shape in invalid_shapes:
      bad=Provider(facts_used=shape);sink=Usage();out=build_generative_content_batch(**base,provider=bad,usage_sink=sink)
      assert out["status"]=="BLOCKED" and out["reason_codes"]==["CONTENT_FACTS_UNGROUNDED"]
      assert out["receipt"]["request_id"]=="req_TEST_ONLY" and out["receipt"]["usage_status"]=="PROVIDER_RETURNED_USAGE"
      assert len(sink.rows)==1 and sink.rows[0]["outcome"]=="FAILED"
    invented=Provider(normalized_facts=[{"field":"title","raw_value":"TEST_ONLY","normalized_value":"invented","evidence":"absent"}])
    invented_out=build_generative_content_batch(**base,provider=invented,usage_sink=Usage())
    assert invented_out["reason_codes"]==["CONTENT_FACTS_EVIDENCE_INVALID"]
    assert invented_out["validation_diagnostic"]=={"stage":"normalized_facts","rule":"content_must_not_create_facts","item_index":0,"field":"title"}
    assert "TEST_ONLY" not in json.dumps(invented_out["validation_diagnostic"])
    # All normalized-fact variants are rejected with a field-safe diagnostic;
    # no source value/evidence text is copied into the diagnostic or receipt.
    invalid_fact_items=(
      {"field":"unknown_field","raw_value":"x","normalized_value":"x","evidence":"TEST_ONLY"},
      {"field":"title","raw_value":"x","normalized_value":"different unit","evidence":"TEST_ONLY"},
      {"field":"title","raw_value":"x","normalized_value":"x","evidence":"absent punctuation"},
      {"field":"title","raw_value":72,"normalized_value":"72 m²","evidence":"TEST_ONLY"},
      {"field":"title","raw_value":"x","normalized_value":"x","evidence":"không dấu tiếng Việt"},
      {"field":"title","raw_value":"x","normalized_value":"x"},
      {"field":"title","raw_value":"x","normalized_value":"x","evidence":"TEST_ONLY"},
      [{"field":"title","raw_value":"x","normalized_value":"x","evidence":"TEST_ONLY"},
       {"field":"title","raw_value":"x","normalized_value":"x","evidence":"TEST_ONLY"}],
      "malformed-item",
    )
    for item in invalid_fact_items:
      bad=Provider(normalized_facts=[item]);sink=Usage()
      rejected=build_generative_content_batch(**base,provider=bad,usage_sink=sink)
      assert rejected["reason_codes"]==["CONTENT_FACTS_EVIDENCE_INVALID"]
      assert rejected["validation_diagnostic"]["rule"]=="content_must_not_create_facts"
      assert rejected["receipt"]["request_id"]=="req_TEST_ONLY" and len(sink.rows)==1
      assert "TEST_ONLY" not in json.dumps(rejected["validation_diagnostic"])
    # Normalized Vietnamese text, units, numeric prices and optional fields
    # pass through as caller-owned revision-bound facts without reinterpretation.
    passthrough={"title":"Căn hộ ở Đắk Lắk","area":"72 m²","optional":None}
    request=build_content_request({"facts":passthrough,"source_text":"Giá thuê 6 triệu/tháng; căn hộ 72 m²"},"gpt-6-luna")
    sent=json.loads(request["input"])["facts"]
    assert sent=={"area":passthrough["area"]}  # request budget permits this field with source text
    assert json.loads(request["input"])["source_text"]=="Giá thuê 6 triệu/tháng; căn hộ 72 m²"
    priced=json.loads(build_content_request({"facts":{"title":"Căn hộ Đắk Lắk","price_vnd_per_month":6000000}},"gpt-6-luna")["input"])["facts"]
    assert priced.get("price_vnd_per_month")==6000000 and isinstance(priced["price_vnd_per_month"],int)
    mismatch=Provider(normalized_facts=[{"field":"title","raw_value":"TEST_ONLY","normalized_value":"different","evidence":"TEST_ONLY"}])
    mismatch_out=build_generative_content_batch(**base,provider=mismatch,usage_sink=Usage())
    assert mismatch_out["reason_codes"]==["CONTENT_FACTS_EVIDENCE_INVALID"]
    normalized_only=Provider(facts_used=["area"],normalized_facts=[{"field":"area","raw_value":"50 m2","normalized_value":"50 m2","evidence":"TEST_ONLY"}])
    normalized_only_out=build_generative_content_batch(**base,provider=normalized_only,usage_sink=Usage())
    assert normalized_only_out["reason_codes"]==["CONTENT_FACTS_EVIDENCE_INVALID"]
    omitted=Provider(facts_used=["description"],input_fact_keys=["title"])
    omitted_out=build_generative_content_batch(**{**base,"facts":{"title":"TEST_ONLY","description":"not sent"}},provider=omitted,usage_sink=Usage())
    assert omitted_out["reason_codes"]==["CONTENT_FACTS_UNGROUNDED"]
    missing_usage=Provider(facts_used=["unknown_key"],usage=None)
    unknown_receipt=build_generative_content_batch(**base,provider=missing_usage,usage_sink=None)["receipt"]
    assert unknown_receipt["usage_status"]=="USAGE_UNKNOWN" and unknown_receipt["cost_status"]=="USAGE_UNKNOWN"
    assert unknown_receipt["estimated_cost_usd"] is None and unknown_receipt["request_id"]=="req_TEST_ONLY"
    assert compute_usd({},official_pricing_catalog()["gpt-6-luna"])["status"]=="USAGE_INCOMPLETE"
    assert compute_usd({"input_tokens":True,"output_tokens":2,"total_tokens":3},official_pricing_catalog()["gpt-6-luna"])["status"]=="USAGE_INCOMPLETE"
    for n in (4,6):
      bad=Provider(count=n);out=build_generative_content_batch(**base,provider=bad,usage_sink=Usage())
      assert out["status"]=="BLOCKED" and out["reason_codes"]==["CONTENT_SCHEMA_INVALID"] and out["receipt"]["request_id"]=="req_TEST_ONLY" and bad.calls==1
    malformed=Provider();malformed.generate=lambda payload:{"variants":[],"normalized_facts":None,"missing_fields":[],"conflicts":[],"usage":DEFAULT_USAGE,"provider_request_id":"req_TEST_ONLY"}
    malformed_result=build_generative_content_batch(**base,provider=malformed,usage_sink=Usage())
    assert malformed_result["reason_codes"]==["CONTENT_SCHEMA_INVALID"]
    assert malformed_result["validation_diagnostic"]=={
      "stage":"response_shape","rule":"five_variants_and_fact_metadata_lists",
      "invalid_fields":["normalized_facts","variants"]}
    class DuplicateContentProvider(Provider):
      def generate(self,payload):
        value=super().generate(payload)
        for variant in value["variants"][1:]:variant["body"]=value["variants"][0]["body"]
        return value
    duplicate=DuplicateContentProvider();duplicate_result=build_generative_content_batch(**base,provider=duplicate,usage_sink=Usage())
    assert duplicate_result["reason_codes"]==["CONTENT_NOT_DISTINCT"]
    assert duplicate.calls==1
    class MissingFactsProvider(Provider):
      def generate(self,payload):
        value=super().generate(payload)
        for variant in value["variants"]:variant.pop("facts_used",None)
        return value
    missing_facts=MissingFactsProvider();missing_result=build_generative_content_batch(**base,provider=missing_facts,usage_sink=Usage())
    assert missing_result["reason_codes"]==["CONTENT_FACTS_UNGROUNDED"] and missing_facts.calls==1
    assert missing_result["validation_diagnostic"]["value_type_invalid"] is True
    class ConflictingProvider(Provider):
      def generate(self,payload):
        value=super().generate(payload);value["conflicts"]=["title"];return value
    conflict_provider=ConflictingProvider()
    conflict_result=build_generative_content_batch(**base,provider=conflict_provider,usage_sink=Usage())
    assert conflict_result["reason_codes"]==["CONTENT_SOURCE_CONFLICT"] and conflict_provider.calls==1
    assert conflict_result["validation_diagnostic"]=={
      "stage":"source_conflicts","rule":"provider_reported_conflict","allowed_field_count":1,"fields":["title"]}
    no_rights=Provider();out=build_generative_content_batch(**{**base,"rights_status":"UNKNOWN"},provider=no_rights,usage_sink=Usage())
    assert out["status"]=="BLOCKED" and no_rights.calls==0
    store.close()
  print("PASS: authenticated loopback HTTP missing-fields 409/provider=0 → same-intake draft save → 201/5 private variants → replay 200; A/B evidence mismatch 409; 0 Property Core writes/publish")
if __name__=="__main__":main()
