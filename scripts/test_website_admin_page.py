#!/usr/bin/env python3
"""TEST_ONLY authenticated HTTP contract for the integrated Website Admin page."""
from __future__ import annotations
import json, os, sys, tempfile, threading, time, urllib.error, urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
  with tempfile.TemporaryDirectory(prefix="sala-website-admin-") as d:
    private=Path(d)/"private";private.mkdir(mode=0o700)
    os.environ.update({"BMT_ADMIN_PRIVATE_ROOT":str(private),"BMT_ADMIN_HOST":"127.0.0.1","BMT_ADMIN_PORT":"0","BMT_ENV":"test","BMT_ADMIN_LOCAL_HTTP_DEV":"1","BMT_ADMIN_OWNER_ID":"owner-test"})
    previous_origin=os.environ.get("BMT_ADMIN_PUBLIC_ORIGIN")
    import admin.admin_server as app
    app.SECURE_COOKIE=False
    app._CONTENT_LIVE_ENABLED=False
    previous_security_check=app.Handler.require_security_config
    app.Handler.require_security_config=lambda self: True
    sid="TEST_ONLY-website-admin-session";app.SESSIONS[sid]=time.time()+60;app.SESSION_OWNERS[sid]="owner-test"
    server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    os.environ["BMT_ADMIN_PUBLIC_ORIGIN"]=base
    try:
      try: urllib.request.urlopen(base+"/website",timeout=5)
      except urllib.error.HTTPError as e: assert e.code==401
      else: raise AssertionError("Website page allowed unauthenticated access")
      req=urllib.request.Request(base+"/website",headers={"Cookie":"bmt_admin_session="+sid})
      with urllib.request.urlopen(req,timeout=5) as r: page=r.read().decode();assert r.status==200 and r.headers.get("Cache-Control")=="no-store"
      for anchor in ("overview","listings","traffic","demand","crm","business","monitor","integrations"):
        assert f'id="{anchor}"' in page
      assert "DEMO" not in page and "CRM trung tâm" in page and "/api/reviews" in page
      rootreq=urllib.request.Request(base+"/",headers={"Cookie":"bmt_admin_session="+sid})
      with urllib.request.urlopen(rootreq,timeout=5) as r:
        rootpage=r.read().decode()
        assert 'href="/website"' in rootpage and 'id="home-dashboard"' in rootpage
        assert 'id="missing-fields-dialog"' in rootpage and 'TIẾP TỤC XỬ LÝ' in rootpage
        assert all(text in rootpage for text in ('Nguồn mới','Content chờ Owner','Tin chờ Website','Tin đang hiển thị'))
        content_handler=rootpage.split('async function createContent(',1)[1].split('const isImageFile=',1)[0]
        assert 'new FormData(intakeForm)' not in content_handler and "JSON.stringify({idempotency_key:key})" in content_handler
      try: urllib.request.urlopen(base+"/api/admin/status",timeout=5)
      except urllib.error.HTTPError as e: assert e.code==401
      else: raise AssertionError("Admin status endpoint allowed unauthenticated access")
      anon_preflight=urllib.request.Request(base+"/api/admin/credential-preflight",data=b"{}",headers={"Origin":base,"Content-Type":"application/json"},method="POST")
      try: urllib.request.urlopen(anon_preflight,timeout=5)
      except urllib.error.HTTPError as e: assert e.code==401
      else: raise AssertionError("Credential preflight allowed unauthenticated access")
      statusreq=urllib.request.Request(base+"/api/admin/status",headers={"Cookie":"bmt_admin_session="+sid})
      with urllib.request.urlopen(statusreq,timeout=5) as r: status=json.loads(r.read());assert r.status==200 and status["content_live_enabled"] is False and status["published"] is False
      os.environ["BMT_ENV"]="staging"
      app._CREDENTIAL_PREFLIGHT_LAST_AT=0.0
      app.resolve_openai_key=lambda **kwargs:"TEST_ONLY_secret_must_not_be_returned"
      preflightreq=urllib.request.Request(base+"/api/admin/credential-preflight",data=b"{}",headers={"Cookie":"bmt_admin_session="+sid,"Origin":base,"Content-Type":"application/json"},method="POST")
      with urllib.request.urlopen(preflightreq,timeout=5) as r:
        raw=r.read();result=json.loads(raw);assert r.status==200 and result=={"status":"READY","credential_source":"KEYCHAIN","request_sent":False,"secret_returned":False}
        assert b"TEST_ONLY_secret_must_not_be_returned" not in raw
      try: urllib.request.urlopen(preflightreq,timeout=5)
      except urllib.error.HTTPError as e: assert e.code==429 and json.loads(e.read())["error"]=="PREFLIGHT_RATE_LIMITED"
      else: raise AssertionError("Credential preflight rate limit did not apply")
      bad_origin=urllib.request.Request(base+"/api/admin/credential-preflight",data=b"{}",headers={"Cookie":"bmt_admin_session="+sid,"Origin":"https://evil.example","Content-Type":"application/json"},method="POST")
      try: urllib.request.urlopen(bad_origin,timeout=5)
      except urllib.error.HTTPError as e: assert e.code==403
      else: raise AssertionError("Credential preflight accepted an untrusted origin")
      app._CREDENTIAL_PREFLIGHT_LAST_AT=0.0
      app.resolve_openai_key=lambda **kwargs:(_ for _ in ()).throw(app.CredentialError("KEYCHAIN_CREDENTIAL_MISSING",44))
      try: urllib.request.urlopen(preflightreq,timeout=5)
      except urllib.error.HTTPError as e:
        result=json.loads(e.read());assert e.code==503 and result["status"]=="MISSING" and result["os_status"]==44 and result["request_sent"] is False
      else: raise AssertionError("Missing Keychain item did not fail closed")
      os.environ["BMT_ENV"]="test"
      print("WEBSITE_ADMIN_HTTP_PASS: auth/no-store; eight Website areas; Home V3 data widgets; missing-fields continuation; Queue no longer uses active-form evidence; AI-off status gated")
    finally:
      server.shutdown();server.server_close();thread.join(timeout=3);app.SESSIONS.pop(sid,None);app.SESSION_OWNERS.pop(sid,None)
      app.Handler.require_security_config=previous_security_check
      if previous_origin is None: os.environ.pop("BMT_ADMIN_PUBLIC_ORIGIN",None)
      else: os.environ["BMT_ADMIN_PUBLIC_ORIGIN"]=previous_origin
if __name__=="__main__":main()
