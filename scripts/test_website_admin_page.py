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
    import admin.admin_server as app
    app.SECURE_COOKIE=False
    app._CONTENT_LIVE_ENABLED=False
    sid="TEST_ONLY-website-admin-session";app.SESSIONS[sid]=time.time()+60;app.SESSION_OWNERS[sid]="owner-test"
    server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
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
      statusreq=urllib.request.Request(base+"/api/admin/status",headers={"Cookie":"bmt_admin_session="+sid})
      with urllib.request.urlopen(statusreq,timeout=5) as r: status=json.loads(r.read());assert r.status==200 and status["content_live_enabled"] is False and status["published"] is False
      print("WEBSITE_ADMIN_HTTP_PASS: auth/no-store; eight Website areas; Home V3 data widgets; missing-fields continuation; Queue no longer uses active-form evidence; AI-off status gated")
    finally:
      server.shutdown();server.server_close();thread.join(timeout=3);app.SESSIONS.pop(sid,None);app.SESSION_OWNERS.pop(sid,None)
if __name__=="__main__":main()
