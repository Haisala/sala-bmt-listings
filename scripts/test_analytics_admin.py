#!/usr/bin/env python3
"""Synthetic handler-level API/auth/CORS tests for Website analytics."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sala-analytics-admin-") as folder:
        private = Path(folder) / "private"
        private.mkdir(mode=0o700)
        os.environ.update({
            "BMT_ADMIN_PRIVATE_ROOT": str(private),
            "BMT_ADMIN_OWNER_ID": "owner-test-only",
            "BMT_ADMIN_HOST": "127.0.0.1",
            "BMT_ADMIN_PORT": "43129",
            "BMT_ENV": "test",
            "BMT_ADMIN_LOCAL_HTTP_DEV": "1",
            "BMT_ANALYTICS_ALLOWED_ORIGINS": "http://127.0.0.1:43130",
            "BMT_ANALYTICS_MAX_PER_MINUTE": "120",
        })
        os.environ.pop("BMT_ADMIN_PUBLIC_ORIGIN", None)
        os.environ.pop("BMT_ADMIN_ALLOWED_IP", None)
        import admin.admin_server as adm

        adm._ANALYTICS_STORE = None
        adm._ANALYTICS_RATE_TIMES.clear()
        property_id = os.environ.get("BMT_TEST_PROPERTY_ID", "property-0123456789abcdef")
        adm._analytics_property_is_public = lambda value: value == property_id

        def call(method: str, path: str, payload=None, *, origin="http://127.0.0.1:43130", owner="owner-test-only", extra_headers=None):
            response = {"headers": {}}
            sid = "test-session-" + uuid.uuid4().hex
            if owner:
                adm.SESSIONS[sid] = time.time() + 60
                adm.SESSION_OWNERS[sid] = owner
            body = json.dumps(payload or {}).encode() if method == "POST" else b""
            headers = {"Content-Length": str(len(body)), "Content-Type": "application/json", "Origin": origin}
            if owner:
                headers["Cookie"] = "bmt_admin_session=" + sid
            headers.update(extra_headers or {})
            h = adm.Handler.__new__(adm.Handler)
            h.path, h.command, h.client_address = path, method, ("127.0.0.1", 43000)
            h.headers, h.rfile, h.wfile = headers, io.BytesIO(body), io.BytesIO()
            h.send_json = lambda value, status=200, headers=None: response.update(status=status, value=value, headers=headers or {})
            h.send_response = lambda status: response.update(status=status)
            h.send_header = lambda key, value: response["headers"].update({key: value})
            h.end_headers = lambda: None
            if method == "POST":
                h.analytics_event_post()
            elif method == "GET":
                h.analytics_report()
            else:
                h.do_OPTIONS()
            return response

        event_id = str(uuid.uuid4())
        payload = {"event_id": event_id, "event_type": os.environ.get("BMT_TEST_EVENT_TYPE", "property_detail_view"), "property_id": property_id}
        accepted = call("POST", "/api/analytics/events", payload)
        assert accepted["status"] == 202 and accepted["value"] == {"accepted": True, "duplicate": False}
        assert accepted["headers"].get("Access-Control-Allow-Origin") == "http://127.0.0.1:43130"
        replay = call("POST", "/api/analytics/events", payload)
        assert replay["status"] == 200 and replay["value"]["duplicate"] is True
        denied_origin = call("POST", "/api/analytics/events", payload, origin="https://attacker.invalid")
        assert denied_origin["status"] == 403 and "Access-Control-Allow-Origin" not in denied_origin["headers"]
        unknown = call("POST", "/api/analytics/events", {**payload, "event_id": str(uuid.uuid4()), "property_id": "property-deadbeef"})
        assert unknown["status"] == 422
        oversized = call("POST", "/api/analytics/events", payload, extra_headers={"Content-Length": "4097"})
        assert oversized["status"] == 413
        invalid_event = call("POST", "/api/analytics/events", {**payload, "event_id": str(uuid.uuid4()), "phone": "0901234567"})
        assert invalid_event["status"] == 422

        preflight = call("OPTIONS", "/api/analytics/events", origin="http://127.0.0.1:43130",
                         extra_headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        assert preflight["status"] == 204
        assert preflight["headers"].get("Access-Control-Allow-Origin") == "http://127.0.0.1:43130"
        assert "Access-Control-Allow-Credentials" not in preflight["headers"]
        denied_preflight = call("OPTIONS", "/api/analytics/events", origin="https://attacker.invalid",
                                extra_headers={"Access-Control-Request-Method": "POST"})
        assert denied_preflight["status"] == 403

        unauth = call("GET", f"/api/analytics/report?from=2026-11-21&to=2026-11-21", owner=None)
        assert unauth["status"] == 401
        wrong_owner = call("GET", "/api/analytics/report?from=2026-11-21&to=2026-11-21", owner="not-owner")
        assert wrong_owner["status"] == 403
        report = call("GET", f"/api/analytics/report?from=2026-11-21&to=2026-11-21&property_id={property_id}")
        assert report["status"] == 200 and report["value"]["total_events"] == 1
        assert report["value"]["by_property"] == [{"property_id": property_id, "event_type": "property_detail_view", "count": 1}]
        assert report["value"]["privacy"]["phone_lead_inferred"] is False

        adm._ANALYTICS_RATE_TIMES.clear()
        os.environ["BMT_ANALYTICS_MAX_PER_MINUTE"] = "1"
        adm._analytics_rate_allowed = lambda: True
        # Exercise the rate guard deterministically through the route.
        adm._analytics_rate_allowed = lambda: False
        limited = call("POST", "/api/analytics/events", {**payload, "event_id": str(uuid.uuid4())})
        assert limited["status"] == 429
        print("ANALYTICS_ADMIN_SYNTHETIC_PASS: ingest/replay/origin/CORS/allowlist/size/PII/rate/Owner-report")


if __name__ == "__main__":
    main()
