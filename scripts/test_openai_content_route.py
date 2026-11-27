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
from property_memory.providers.openai_provider import ProviderError

DEFAULT_FACTS_USED=object()
DEFAULT_USAGE=object()
DEFAULT_INPUT_KEYS=object()

class Noop:
    def audit(self,*a,**k): pass
    def enqueue(self,*a,**k): return {"status":"queued"}
class Provider:
    provider_name="TEST_ONLY_MOCK";model="gpt-6-luna"
    def __init__(self,count=5,facts_used=DEFAULT_FACTS_USED,normalized_facts=None,usage=DEFAULT_USAGE,input_fact_keys=DEFAULT_INPUT_KEYS):
        self.calls=0;self.count=count;self.facts_used=facts_used;self.normalized_facts=normalized_facts or [];self.usage=usage;self.input_fact_keys=input_fact_keys;self.last_payload=None
    def generate(self,payload):
        self.calls+=1;self.last_payload=payload
        assert payload["output_count"]==5 and payload["media_analysis"]=="NOT_SENT"
        used=["title"] if self.facts_used is DEFAULT_FACTS_USED else self.facts_used
        usage={"input_tokens":100,"output_tokens":300,"total_tokens":400} if self.usage is DEFAULT_USAGE else self.usage
        result={"variants":[{"variant_index":i,"title":f"Fixture {i}","body":f"Distinct synthetic draft {i}","facts_used":used,"citations_used":[],"unknowns":[],"warnings":[]} for i in range(1,self.count+1)],
                "normalized_facts":self.normalized_facts,"missing_fields":["area"],"conflicts":[],
                "usage":usage,"provider_request_id":"req_TEST_ONLY"}
        if self.input_fact_keys is not DEFAULT_INPUT_KEYS:result["input_fact_keys"]=self.input_fact_keys
        return result
class PreflightProvider:
    provider_name="TEST_ONLY_PREFLIGHT";model="gpt-6-luna"
    def generate(self,payload): raise ProviderError("CONTENT_INPUT_LIMIT_EXCEEDED","TEST_ONLY bounded failure",dispatch_started=False)
class Usage:
    def __init__(self):self.rows=[]
    def record(self,**kw):
        self.rows.append(kw);return {"status":"ESTIMATE_ONLY","usd_cost":"0.0001","tokens":kw["usage"]}

def call(handler,intake_id,key,payload=None):
    out={};handler.send_json=lambda value,status=200:out.update(value=value,status=status)
    admin.Handler.create_content_from_intake(handler,intake_id,{"idempotency_key":key,
      "source_media_rights_confirmed":True,"external_text_processing_consent":True,**(payload or {})});return out

def main():
  with tempfile.TemporaryDirectory(prefix="sala-one-touch-test-") as folder:
    root=Path(folder); private=root/"private";private.mkdir(mode=0o700)
    inbox=private/"inbox";inbox.mkdir(mode=0o700)
    with patch.object(admin,"PRIVATE_ROOT",private),patch.object(admin,"INBOX_ROOT",inbox),patch.object(admin,"INDEX_PATH",private/"index.json"),patch.object(admin,"AUDIT_LOG_PATH",private/"audit.jsonl"),patch.object(admin,"ERROR_QUEUE_PATH",private/"errors.json"):
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
      "factProvenance":{"title":{"evidence_excerpt":"synthetic 72 square meter"},
        "address":{"evidence_excerpt":"TEST_ONLY district"}},
      "ownerEvidence":{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture","rights_status":"PERSONAL"},
      "media":[{"media_id":f"TEST_ONLY-media-{i:02d}","filename":f"TEST_ONLY-{i:02d}.jpg",
                "path":f"inbox/TEST_ONLY/{i:02d}.jpg","sha256":f"{i:064x}","bytes":128}
               for i in range(10)]}
    pkg=process_intake(store,center,intake,ai_gateway=None,operation_id=intake["id"])
    pkg=center.packages[pkg["review_id"]]
    pkg["evidence_revisions"]=[{"evidence_revision_id":"TEST_ONLY-evidence-r1","evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture","rights_status":"PERSONAL"}]}}]
    center._persist(pkg)
    missing={"id":"TEST_ONLY-intake-no-rights","source":"TEST_ONLY","note":"TEST_ONLY synthetic source with title: TEST_ONLY rental home","preliminaryFacts":{"title":"TEST_ONLY rental home","property_type":"APARTMENT","transaction_type":"RENT","price_vnd_per_month":6000000,"price_cycle":"MONTH","address":"TEST_ONLY address","area":"72 m²"},"media":[]}
    provider=Provider();usage=Usage()
    handler=SimpleNamespace(owner_id=lambda:"owner",review_center_or_error=lambda:center)
    records=[intake,missing]
    with patch.dict(os.environ,{"BMT_ENV":"test","BMT_TEST_ONLY":"1","SALA_E2E_ROOT":folder,"BMT_ADMIN_OWNER_ID":"owner"}), \
         patch.object(admin,"PRIVATE_ROOT",private),patch.object(admin,"AUDIT_LOG_PATH",private/"audit.jsonl"),patch.object(admin,"ERROR_QUEUE_PATH",private/"errors.json"),patch.object(admin,"HOST","127.0.0.1"), \
         patch.object(admin,"_CONTENT_LIVE_ENABLED",True),patch.object(admin,"_AI_PROVIDER",provider),patch.object(admin,"_USAGE_LEDGER",usage), \
         patch.object(admin,"load_index",lambda:records),patch.object(admin,"save_index",lambda items:None):
      before_packages=len(center.packages)
      consent_blocked=call(handler,intake["id"],"TEST_ONLY-missing-rights-confirmation-001",{"source_media_rights_confirmed":False})
      assert consent_blocked["status"]==409
      assert consent_blocked["value"]["error"]=="CONTENT_SOURCE_MEDIA_RIGHTS_CONFIRMATION_REQUIRED"
      assert consent_blocked["value"]["provider_calls"]==0 and provider.calls==0
      assert len(center.packages)==before_packages and store.list_properties()==[]
      external_consent_blocked=call(handler,intake["id"],"TEST_ONLY-missing-paid-consent-001",{"external_text_processing_consent":False})
      assert external_consent_blocked["status"]==409
      assert external_consent_blocked["value"]["error"]=="CONTENT_EXTERNAL_PROCESSING_CONSENT_REQUIRED"
      assert external_consent_blocked["value"]["provider_calls"]==0 and provider.calls==0
      assert len(center.packages)==before_packages and store.list_properties()==[]
      result=call(handler,intake["id"],"TEST_ONLY-idempotency-key-001")
      assert result["status"]==201,result
      body=result["value"]
      assert len(body["variants"])==5 and body["published"] is False and body["mutated_property_store"] is False
      assert intake["status"]=="CONTENT_CREATED" and intake["contentReviewId"]==body["review_id"]
      assert body["media_analysis"]=="NOT_SENT" and provider.calls==1 and len(usage.rows)==1
      assert len(center.packages[body["review_id"]]["candidate"]["media"])==10
      assert provider.last_payload["media"]==[]
      assert "synthetic 72 square meter" in provider.last_payload.get("source_text","")
      assert provider.last_payload.get("source_text","") != intake["note"]
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
      # A fresh browser key after reload must recover the same five drafts,
      # not create another billable generation.
      cross_key_recovery=call(handler,intake["id"],"TEST_ONLY-new-browser-key-002")
      assert cross_key_recovery["status"]==200 and cross_key_recovery["value"]["idempotent_replay"] is True
      assert cross_key_recovery["value"]["content_batch_id"]==body["content_batch_id"]
      assert provider.calls==1 and len(usage.rows)==1
      pending_package=center.packages[body["review_id"]]
      selected_variant=pending_package["content_batches"][0]["variants"][0]
      select_out={}
      select_handler=SimpleNamespace(owner_id=lambda:"owner",review_center_or_error=lambda:center,
        send_json=lambda value,status=200:select_out.update(value=value,status=status))
      admin.Handler.content_route_post(select_handler,body["review_id"],"select",{
        "content_variant_id":selected_variant["content_variant_id"],
        "expected_content_revision":selected_variant["content_revision"]})
      assert select_out["status"]==200 and pending_package["selected_content_variant_id"]==selected_variant["content_variant_id"]
      assert pending_package["state"]!="APPROVED" and store.list_properties()==[]
      assert not pending_package["content_batches"][0].get("property_id")
      assert provider.calls==1 and len(usage.rows)==1
      persisted=center.packages[pkg["review_id"]]
      assert len(persisted["content_batches"])==1 and len(persisted["content_batches"][0]["variants"])==5
      assert persisted["content_batches"][0]["status"]=="PRIVATE_DRAFT"
      assert store.list_properties()==[]
      uncertain_intake={"id":"TEST_ONLY-intake-unknown","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-unknown",
        "note":"TEST_ONLY synthetic source","preliminaryFacts":dict(intake["preliminaryFacts"]),
        "ownerEvidence":dict(intake["ownerEvidence"]),"media":[]}
      records.append(uncertain_intake)
      uncertain_pkg=process_intake(store,center,uncertain_intake,ai_gateway=None,operation_id=uncertain_intake["id"])
      uncertain_pkg=center.packages[uncertain_pkg["review_id"]]
      uncertain_pkg.setdefault("content_generation_requests",[]).append({"status":"BLOCKED",
        "response":{"error":"CONTENT_PROVIDER_ERROR","provider_calls":1,"usage":"UNKNOWN"}})
      center._persist(uncertain_pkg)
      unknown_replay=call(handler,uncertain_intake["id"],"TEST_ONLY-different-key-after-unknown-003")
      assert unknown_replay["status"]==409
      assert unknown_replay["value"]["error"]=="CONTENT_REQUEST_OUTCOME_UNKNOWN"
      assert unknown_replay["value"]["previous_attempt_unverified"] is True
      assert provider.calls==1 and len(usage.rows)==1 and store.list_properties()==[]
      attempted_unknown_retry=call(handler,uncertain_intake["id"],"TEST_ONLY-ambiguous-retry-004",{"retry_preflight":True})
      assert attempted_unknown_retry["status"]==409
      assert attempted_unknown_retry["value"]["error"]=="CONTENT_REQUEST_OUTCOME_UNKNOWN"
      assert provider.calls==1 and len(usage.rows)==1
      safe_intake={"id":"TEST_ONLY-intake-preflight","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-preflight",
        "note":"TEST_ONLY synthetic source","preliminaryFacts":dict(intake["preliminaryFacts"]),
        "ownerEvidence":dict(intake["ownerEvidence"]),"media":[]}
      records.append(safe_intake)
      with patch.object(admin,"_AI_PROVIDER",PreflightProvider()),patch.object(admin,"_USAGE_LEDGER",Usage()):
        preflight=call(handler,safe_intake["id"],"TEST_ONLY-preflight-first-001")
      assert preflight["status"]==422 and preflight["value"]["error"]=="CONTENT_INPUT_LIMIT_EXCEEDED"
      assert preflight["value"]["provider_calls"]==0 and preflight["value"]["dispatch_started"] is False
      safe_package=next(p for p in center.packages.values() if p["candidate"]["intake_id"]==safe_intake["id"])
      status=admin.content_attempt_status(safe_intake["id"],safe_intake,center)
      assert status["state"]=="SAFE_RETRY_AVAILABLE" and status["can_retry"] is True
      assert status["consent"]=={"source_media_rights_confirmed":True,"external_text_processing_consent":True}
      assert admin.reusable_content_consent(safe_package,{**safe_intake,"inputRevision":2},"gpt-6-luna")=={
        "source_media_rights_confirmed":False,"external_text_processing_consent":False}
      assert admin.reusable_content_consent(safe_package,safe_intake,"different-model")=={
        "source_media_rights_confirmed":False,"external_text_processing_consent":False}
      blocked_retry=call(handler,safe_intake["id"],"TEST_ONLY-preflight-without-confirmation-002",
        {"source_media_rights_confirmed":False,"external_text_processing_consent":False})
      assert blocked_retry["status"]==409 and blocked_retry["value"]["error"]=="CONTENT_SAFE_RETRY_CONFIRMATION_REQUIRED"
      retry_provider=Provider();retry_usage=Usage()
      with patch.object(admin,"_AI_PROVIDER",retry_provider),patch.object(admin,"_USAGE_LEDGER",retry_usage):
        retried=call(handler,safe_intake["id"],"TEST_ONLY-preflight-explicit-retry-003",
          {"source_media_rights_confirmed":False,"external_text_processing_consent":False,"retry_preflight":True})
      assert retried["status"]==201 and len(retried["value"]["variants"])==5
      assert retry_provider.calls==1 and len(retry_usage.rows)==1 and store.list_properties()==[]
      assert len(safe_package["content_generation_requests"])==2
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
      "note":"TEST_ONLY Apartment available for rent; address EcoCity.","preliminaryFacts":{"transaction_type":"RENT"},
      "factProvenance":{"title":{"evidence_excerpt":"TEST_ONLY Apartment"}},
      "ownerEvidence":{"source_id":"TEST_ONLY-http-source","claim":"TEST_ONLY source","rights_status":"PERSONAL"},"inputRevision":1,"media":[]}
    (http_private/"index.json").write_text(json.dumps([http_intake]))
    http_provider=Provider(normalized_facts=[{"field":"title","raw_value":"Apartment",
      "normalized_value":"TEST_ONLY Apartment","evidence":"Apartment"}]);http_usage=Usage();sid="TEST_ONLY-session"
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
      except urllib.error.HTTPError as e: rights_status=e.code;rights_body=json.loads(e.read())
      else: raise AssertionError("missing media/source-rights confirmation unexpectedly dispatched")
      assert rights_status==409 and rights_body["error"]=="CONTENT_SOURCE_MEDIA_RIGHTS_CONFIRMATION_REQUIRED"
      assert rights_body["provider_calls"]==0 and http_provider.calls==0 and http_center.packages=={}
      rights_only_request=urllib.request.Request(url,data=json.dumps({"idempotency_key":"TEST_ONLY-http-idempotency-001",
        "source_media_rights_confirmed":True,"external_text_processing_consent":False}).encode(),method="POST",headers=request.headers)
      try: urllib.request.urlopen(rights_only_request,timeout=5)
      except urllib.error.HTTPError as e: consent_status=e.code;consent_body=json.loads(e.read())
      else: raise AssertionError("missing external-processing consent unexpectedly dispatched")
      assert consent_status==409 and consent_body["error"]=="CONTENT_EXTERNAL_PROCESSING_CONSENT_REQUIRED"
      assert consent_body["provider_calls"]==0 and http_provider.calls==0 and http_center.packages=={}
      consented_request=urllib.request.Request(url,data=json.dumps({"idempotency_key":"TEST_ONLY-http-idempotency-001",
        "source_media_rights_confirmed":True,"external_text_processing_consent":True}).encode(),method="POST",headers=request.headers)
      try:
        with urllib.request.urlopen(consented_request,timeout=5) as response: status=response.status;http_body=json.loads(response.read())
      except urllib.error.HTTPError as exc:
        raise AssertionError(f"TEST_ONLY consented generation returned HTTP {exc.code}: {json.loads(exc.read())}")
      assert status==201 and len(http_body["variants"])==5 and http_body["published"] is False
      saved_package=http_center.packages[http_body["review_id"]]
      assert saved_package["candidate"]["facts"].get("title")=="TEST_ONLY Apartment"
      assert saved_package["candidate"]["fact_provenance"].get("title",{}).get("origin")=="AI_EXTRACTED"
      assert saved_package["candidate"].get("missing_fields",[])
      with urllib.request.urlopen(consented_request,timeout=5) as response: replay_status=response.status;replay_body=json.loads(response.read())
      assert replay_status==200 and replay_body["content_batch_id"]==http_body["content_batch_id"]
      edit_url=f"http://127.0.0.1:{server.server_port}/api/reviews/{http_body['review_id']}/edit"
      edit_req=urllib.request.Request(edit_url,data=json.dumps({"expected_revision":http_body["review_revision"],"field":"area","value":"72 m²"}).encode(),method="POST",headers=request.headers)
      with urllib.request.urlopen(edit_req,timeout=5) as response: edit_status=response.status;edited=json.loads(response.read())
      assert edit_status==200 and edited["review_revision"]==http_body["review_revision"]+1
      refresh_req=urllib.request.Request(url,data=json.dumps({"idempotency_key":"TEST_ONLY-explicit-refresh-002","refresh_content":True}).encode(),method="POST",headers=request.headers)
      with urllib.request.urlopen(refresh_req,timeout=5) as response: refresh_status=response.status;refreshed=json.loads(response.read())
      assert refresh_status==201 and len(refreshed["variants"])==5 and refreshed["content_batch_id"]!=http_body["content_batch_id"]
      refreshed_package=http_center.packages[http_body["review_id"]]
      assert len(refreshed_package["content_batches"])==2
      assert refreshed_package["content_batches"][0]["content_batch_id"]==http_body["content_batch_id"]
      assert refreshed_package["content_batches"][1]["review_revision"]==edited["review_revision"]
      assert http_provider.calls==2 and len(http_usage.rows)==2 and http_store.list_properties()==[]
      assert http_provider.calls==2 and len(http_usage.rows)==2 and http_store.list_properties()==[]
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
      assert http_provider.calls==2 and len(http_usage.rows)==2
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
    assert invented_out["validation_diagnostic"]=={"stage":"normalized_facts","rule":"allowlisted_field_raw_value_and_exact_source_excerpt","item_index":0,"field":"title"}
    assert "TEST_ONLY" not in json.dumps(invented_out["validation_diagnostic"])
    # Unsupported normalized-fact variants are rejected with a field-safe diagnostic;
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
      assert rejected["validation_diagnostic"]["rule"]=="allowlisted_field_raw_value_and_exact_source_excerpt"
      assert rejected["receipt"]["request_id"]=="req_TEST_ONLY" and len(sink.rows)==1
      assert "TEST_ONLY" not in json.dumps(rejected["validation_diagnostic"])
    # Normalized Vietnamese text, units, numeric prices and optional fields
    # pass through as caller-owned revision-bound facts without reinterpretation.
    passthrough={"title":"Căn hộ ở Đắk Lắk","area":"72 m²","optional":None}
    request=build_content_request({"facts":passthrough,"source_text":"Giá thuê 6 triệu/tháng; căn hộ 72 m²"},"gpt-6-luna")
    sent=json.loads(request["input"])["facts"]
    assert sent=={"title":passthrough["title"],"area":passthrough["area"]}  # priority facts fit with the optional source excerpt
    assert json.loads(request["input"])["source_text"]=="Giá thuê 6 triệu/tháng; căn hộ 72 m²"
    priced=json.loads(build_content_request({"facts":{"title":"Căn hộ Đắk Lắk","price_vnd_per_month":6000000}},"gpt-6-luna")["input"])["facts"]
    assert priced.get("price_vnd_per_month")==6000000 and isinstance(priced["price_vnd_per_month"],int)
    mismatch=Provider(normalized_facts=[{"field":"title","raw_value":"TEST_ONLY","normalized_value":"different","evidence":"TEST_ONLY"}])
    mismatch_out=build_generative_content_batch(**base,provider=mismatch,usage_sink=Usage())
    assert mismatch_out["status"]=="PASS" and mismatch_out["normalized_facts"][0]["normalized_value"]=="different"
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
    assert conflict_result["reason_codes"]==["CONTENT_FACTS_UNGROUNDED"] and conflict_provider.calls==1
    assert conflict_result["validation_diagnostic"]["stage"]=="variant_facts_used"
    no_rights=Provider();out=build_generative_content_batch(**{**base,"rights_status":"UNKNOWN"},provider=no_rights,usage_sink=Usage())
    assert out["status"]=="BLOCKED" and no_rights.calls==0
    # Same synthetic Content batch now continues through Gate A and Gate B:
    # draft selection before Gate A is private, then the approved variant is
    # linked to canonical property/revision without publishing a projection.
    gate_package=center.packages[body["review_id"]]
    gate_result=center.approve(gate_package["review_id"],expected_revision=gate_package["review_revision"],
      owner_id="owner",decision="CREATE_NEW_PROPERTY",idempotency_key="TEST_ONLY-gate-a-content-001")
    property_id=gate_result["mutation_result"]["property_id"]
    approved_variant=next(v for batch in gate_result["content_batches"] for v in batch["variants"]
      if v["content_variant_id"]==gate_result["selected_content_variant_id"])
    property_revision=center.store.get(property_id)["property_revision"]
    assert approved_variant.get("property_id")==property_id
    assert approved_variant.get("property_revision")==property_revision
    assert approved_variant.get("facts_revision_id")==f"facts-{property_id}-r{property_revision}"
    route_result={};route_handler=SimpleNamespace(owner_id=lambda:"owner",review_center_or_error=lambda:center,
      send_json=lambda value,status=200:route_result.update(value=value,status=status))
    route_result={};admin.Handler.content_route_post(route_handler,gate_package["review_id"],"approve-channel",{
      "content_variant_id":approved_variant["content_variant_id"],
      "expected_content_revision":approved_variant["content_revision"],"channel":"public_web"})
    linked=center.packages[gate_package["review_id"]].get("selected_content") or {}
    assert route_result["status"]==200 and route_result["value"]["published"] is False
    assert linked.get("property_id")==property_id and linked.get("property_revision")==center.store.get(property_id)["property_revision"]
    assert linked.get("content_variant_id")==approved_variant["content_variant_id"]
    assert center.packages[gate_package["review_id"]]["state"]=="APPROVED"
    assert len(store.list_properties())==1
    no_content_intake={"id":"TEST_ONLY-intake-gate-a-no-content","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-gate-a",
      "note":"TEST_ONLY synthetic no-content candidate","preliminaryFacts":{"title":"TEST_ONLY independent property",
      "property_type":"HOUSE","transaction_type":"SALE","price_vnd":2100000000,
      "address":"TEST_ONLY independent address","area":"88 m²","house_type":"TEST_ONLY house",
      "bedrooms":3,"bathrooms":2},"ownerEvidence":{"source_id":"TEST_ONLY-source-independent",
      "claim":"TEST_ONLY synthetic rights evidence","rights_status":"COMPANY"},"media":[]}
    no_content_package=process_intake(store,center,no_content_intake,ai_gateway=None,operation_id=no_content_intake["id"])
    approved_without_content=center.approve(no_content_package["review_id"],expected_revision=1,owner_id="owner",
      decision="CREATE_NEW_PROPERTY",idempotency_key="TEST_ONLY-gate-a-without-content-001")
    assert approved_without_content["state"]=="APPROVED"
    assert not approved_without_content.get("selected_content")
    assert approved_without_content["mutation_result"]["property_id"]
    assert len(approved_without_content.get("content_batches",[]))==0
    conflict_intake={"id":"TEST_ONLY-intake-conflict-confirm","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-conflict-confirm",
      "note":"TEST_ONLY synthetic conflict source","preliminaryFacts":{"title":"TEST_ONLY Owner choice","property_type":"HOUSE",
      "transaction_type":"SALE","price_vnd":2100000000,"address":"TEST_ONLY conflict address","area":"88 m²",
      "house_type":"TEST_ONLY house","bedrooms":3,"bathrooms":2},"media":[]}
    conflict_package=process_intake(store,center,conflict_intake,ai_gateway=None,operation_id=conflict_intake["id"])
    conflict_package=center.packages[conflict_package["review_id"]]
    conflict_package["candidate"]["conflicts"]=[{"field":"title","kind":"AI_SOURCE_CONFLICT","current_value":"Old","source_value":"New","evidence":"TEST_ONLY"}]
    center._persist(conflict_package)
    conflict_confirmed=center.edit(conflict_package["review_id"],expected_revision=1,field="title",
      value="TEST_ONLY Owner choice",owner_id="owner")
    assert conflict_confirmed["candidate"]["conflicts"]==[]
    assert conflict_confirmed["owner_edits"][-1]["origin"]=="OWNER_CONFIRMED"
    ui=Path(__file__).resolve().parents[1]/"admin"/"intake_workflow_ui.py"
    ui_text=ui.read_text(encoding="utf-8")
    assert "approve.disabled=approve.disabled||(hasVariants&&!selected)" not in ui_text
    assert "Gate A chỉ duyệt dữ kiện" in ui_text
    store.close()
  print("PASS: one mock generation → five private drafts → select before Gate A → Gate A → linked public_web draft approval; no publication")
if __name__=="__main__":main()
