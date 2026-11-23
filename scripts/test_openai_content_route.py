"""TEST_ONLY one-touch Intake -> Review -> five private Content contract."""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import admin.admin_server as admin
from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.review_center import ReviewCenter

class Noop:
    def audit(self,*a,**k): pass
    def enqueue(self,*a,**k): return {"status":"queued"}
class Provider:
    provider_name="TEST_ONLY_MOCK";model="gpt-6-luna"
    def __init__(self,count=5):self.calls=0;self.count=count
    def generate(self,payload):
        self.calls+=1
        assert payload["output_count"]==5 and payload["media_analysis"]=="NOT_SENT"
        return {"variants":[{"variant_index":i,"title":f"Fixture {i}","body":f"Distinct synthetic draft {i}","facts_used":["title"],"citations_used":[],"unknowns":[],"warnings":[]} for i in range(1,self.count+1)],
                "normalized_facts":[],"missing_fields":["area"],"conflicts":[],
                "usage":{"input_tokens":100,"output_tokens":300,"total_tokens":400},"provider_request_id":"req_TEST_ONLY"}
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
    store=PropertyStore(private/"store.sqlite3"); noop=Noop()
    center=ReviewCenter(store,reliability=ReliabilityBoundary(noop.audit,noop.enqueue))
    intake={"id":"TEST_ONLY-intake-001","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-fixture",
      "note":"TEST_ONLY synthetic 72 square meter home in TEST_ONLY district",
      "preliminaryFacts":{"title":"TEST_ONLY-home","property_type":"HOUSE"},"media":[]}
    pkg=process_intake(store,center,intake,ai_gateway=None,operation_id=intake["id"])
    pkg=center.packages[pkg["review_id"]]
    pkg["evidence_revisions"]=[{"evidence_revision_id":"TEST_ONLY-evidence-r1","evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-source","claim":"TEST_ONLY synthetic fixture"}]}}]
    center._persist(pkg)
    missing={"id":"TEST_ONLY-intake-no-rights","source":"TEST_ONLY","note":"TEST_ONLY synthetic source with title: TEST_ONLY rental home","preliminaryFacts":{"title":"TEST_ONLY rental home","property_type":"HOUSE"},"media":[]}
    provider=Provider();usage=Usage()
    handler=SimpleNamespace(owner_id=lambda:"owner",review_center_or_error=lambda:center)
    with patch.dict(os.environ,{"BMT_ENV":"test","BMT_TEST_ONLY":"1","SALA_E2E_ROOT":folder,"BMT_ADMIN_OWNER_ID":"owner"}), \
         patch.object(admin,"PRIVATE_ROOT",private),patch.object(admin,"HOST","127.0.0.1"), \
         patch.object(admin,"_CONTENT_LIVE_ENABLED",True),patch.object(admin,"_AI_PROVIDER",provider),patch.object(admin,"_USAGE_LEDGER",usage), \
         patch.object(admin,"load_index",lambda:[intake,missing]):
      result=call(handler,intake["id"],"TEST_ONLY-idempotency-key-001")
      assert result["status"]==201,result
      body=result["value"]
      assert len(body["variants"])==5 and body["published"] is False and body["mutated_property_store"] is False
      assert body["media_analysis"]=="NOT_SENT" and provider.calls==1 and len(usage.rows)==1
      assert body["generation"]["content_variant_ids"]==[v["content_variant_id"] for v in body["variants"]]
      assert body["generation"]["estimated_cost_usd"]=="0.0001"
      assert len(usage.rows[0]["usage"])==3 and usage.rows[0]["review_id"]==pkg["review_id"]
      denied_rights=call(handler,missing["id"],"TEST_ONLY-no-rights-key-001")
      assert denied_rights["status"]==409 and "CONTENT_RIGHTS_REQUIRED" in denied_rights["value"]["blockers"]
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
      valid_evidence={"evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-owner-source","claim":"TEST_ONLY owner-confirmed synthetic source","rights_status":"PERSONAL"}]}}
      generated=call(handler,missing["id"],"TEST_ONLY-evidence-idempotency-001",valid_evidence)
      assert generated["status"]==201 and len(generated["value"]["variants"])==5 and provider.calls==2,generated
      generated_review=center.packages[generated["value"]["review_id"]]
      assert len(generated_review["evidence_revisions"])==1
      assert generated_review["evidence_revisions"][0]["origin"]=="OWNER_CONFIRMED"
      conflict=call(handler,missing["id"],"TEST_ONLY-evidence-conflict-001",{"evidence":{"rights_status":"COMPANY","citations":[{"source_id":"TEST_ONLY-other","claim":"TEST_ONLY different source","rights_status":"COMPANY"}]}})
      assert conflict["status"]==409 and conflict["value"]["error"]=="EVIDENCE_REVISION_EXISTS" and provider.calls==2
    # Exercise the actual authenticated POST route over loopback with TEST_ONLY storage.
    import json, threading, urllib.error, urllib.request
    from http.server import ThreadingHTTPServer
    http_root=root/"http"; http_root.mkdir(mode=0o700)
    http_private=http_root/"private"; http_private.mkdir(mode=0o700)
    http_store=PropertyStore(http_private/"store.sqlite3")
    http_center=ReviewCenter(http_store,reliability=ReliabilityBoundary(noop.audit,noop.enqueue))
    http_intake={"id":"TEST_ONLY-intake-http-001","source":"TEST_ONLY","sourceGroup":"TEST_ONLY-http-fixture",
      "note":"TEST_ONLY synthetic source text","preliminaryFacts":{"title":"TEST_ONLY HTTP property"},"media":[]}
    http_pkg=process_intake(http_store,http_center,http_intake,ai_gateway=None,operation_id=http_intake["id"])
    http_pkg=http_center.packages[http_pkg["review_id"]]
    http_pkg["evidence_revisions"]=[{"evidence_revision_id":"TEST_ONLY-http-evidence","evidence":{"rights_status":"PERSONAL","citations":[{"source_id":"TEST_ONLY-http-source","claim":"TEST_ONLY source"}]}}]
    http_center._persist(http_pkg)
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
      with urllib.request.urlopen(request,timeout=5) as response: status=response.status;http_body=json.loads(response.read())
      assert status==201 and len(http_body["variants"])==5 and http_body["published"] is False
      with urllib.request.urlopen(request,timeout=5) as response: replay_status=response.status;replay_body=json.loads(response.read())
      assert replay_status==200 and replay_body["content_batch_id"]==http_body["content_batch_id"]
      assert http_provider.calls==1 and len(http_usage.rows)==1 and http_store.list_properties()==[]
      server.shutdown();server.server_close();thread.join(timeout=3);admin.SESSIONS.pop(sid,None);admin.SESSION_OWNERS.pop(sid,None);http_store.close()

    from property_memory.content_studio import build_generative_content_batch
    base=dict(generation_id="TEST_ONLY-malformed",facts={"title":"TEST_ONLY"},provenance={"intake_id":intake["id"]},
      rights_status="PERSONAL",media=[],model="gpt-6-luna",source_text="TEST_ONLY text",usage_sink=usage)
    for n in (4,6):
      bad=Provider(count=n);out=build_generative_content_batch(**base,provider=bad)
      assert out["status"]=="BLOCKED" and bad.calls==1
    no_rights=Provider();out=build_generative_content_batch(**{**base,"rights_status":"UNKNOWN"},provider=no_rights)
    assert out["status"]=="BLOCKED" and no_rights.calls==0
    store.close()
  print("PASS: authenticated loopback HTTP one-touch 201→5 private variants, replay 200; Owner evidence persisted once; missing/conflicting rights fail closed; 0 Property Core writes/publish")
if __name__=="__main__":main()
