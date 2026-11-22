"""TEST_ONLY HTTP proof that Admin Owner auth controls Website publication."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "TEST_ONLY-single-owner-session-2026"
EXPIRED_SESSION_ID = "TEST_ONLY-expired-session-fixture"
OTHER_OWNER_SESSION_ID = "TEST_ONLY-other-owner-session-fixture"


def call(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    request_headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=8) as response:
            status, response_headers, raw = response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        status, response_headers, raw = exc.code, dict(exc.headers), exc.read()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = raw.decode("utf-8", "replace")
    return status, response_headers, parsed


def record(evidence: dict[str, Any], label: str, expected: int, actual: int) -> None:
    evidence[label] = f"HTTP {actual}"
    if actual != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, received {actual}")


def intake_fixture(identifier: str) -> dict[str, Any]:
    return {
        "id": identifier, "source": "TEST_ONLY", "sourceGroup": "SYNTHETIC",
        "note": "TEST_ONLY single-session publication fixture",
        "preliminaryFacts": {
            "title": "TEST_ONLY session proof property", "property_type": "HOUSE",
            "transaction_type": "SALE", "address": "TEST_ONLY address",
            "area": "100 m²", "width": 5, "length": 20,
            "land_area": 100, "price_vnd": 1900000000,
            "legal_status": "CERTIFICATE", "house_type": "TEST_ONLY home",
            "bedrooms": 2, "bathrooms": 1,
        },
        "missingFields": [], "conflicts": [], "rightsStatus": "UNKNOWN", "media": [],
    }


def child_env(root: Path, role: str, port: int = 0) -> dict[str, str]:
    env = {
        "PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(root / "tmp"),
        "SALA_E2E_ROOT": str(root), "SALA_E2E_MODE": role,
    }
    if role == "admin":
        env.update({
            "BMT_ADMIN_PRIVATE_ROOT": str(root / "admin-private"),
            "BMT_ADMIN_PREVIEW_ROOT": str(root / "tmp" / "admin-preview"),
            "BMT_ADMIN_HOST": "127.0.0.1", "BMT_ADMIN_PORT": str(port),
            "BMT_ADMIN_PUBLIC_ORIGIN": "", "BMT_ADMIN_ALLOWED_IP": "",
            "BMT_ADMIN_OWNER_ID": "TEST_ONLY_OWNER", "BMT_ADMIN_PASSWORD_FILE": str(root / "owner-password"),
            "BMT_ADMIN_LOCAL_HTTP_DEV": "1", "BMT_ADMIN_SECURE_COOKIE": "0",
            "BMT_ENV": "test", "BMT_OPENAI_EXTRACTION": "0", "BMT_OPENAI_CONTENT_LIVE": "0",
        })
    else:
        env.update({
            "SALA_BIND_HOST": "127.0.0.1", "SALA_PORT": str(port),
            "SALA_DB_PATH": str(root / "customer-private" / "legacy-customer.sqlite3"),
            "SALA_ALLOWED_ORIGINS": "",
            "SALA_PUBLIC_PROJECTION_PATH": str(root / "tmp" / "admin-preview" / "data" / "listings.json"),
            "SALA_PUBLIC_ASSETS_ROOT": str(root / "public-assets"),
        })
    return env


def child() -> None:
    role = os.environ["SALA_E2E_MODE"]
    root = Path(os.environ["SALA_E2E_ROOT"])
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if role == "admin":
        import admin.admin_server as adm
        import property_memory.projection_builder as projection_builder
        from property_memory import PropertyStore
        from property_memory.intake_review import process_intake
        from property_memory.review_center import ReviewCenter

        adm.ensure_private()
        assets = root / "public-assets"
        projection_builder.PROJECT_ROOT = assets
        store = PropertyStore(root / "admin-private" / "property-core.sqlite3")
        center = ReviewCenter(store)
        package = process_intake(store, center, intake_fixture("intake-TEST_ONLY-single-session"))
        approved = center.approve(package["review_id"], expected_revision=1, owner_id="TEST_ONLY_OWNER",
                                  decision="CREATE_NEW_PROPERTY", idempotency_key="TEST_ONLY-gate-a-single-session")
        review_id = approved["review_id"]
        property_id = approved["mutation_result"]["property_id"]
        adm.configure_review_center(center)
        adm.SESSIONS[EXPIRED_SESSION_ID] = time.time() - 30
        adm.SESSION_OWNERS[EXPIRED_SESSION_ID] = "TEST_ONLY_OWNER"
        adm.SESSIONS[OTHER_OWNER_SESSION_ID] = time.time() + 300
        adm.SESSION_OWNERS[OTHER_OWNER_SESSION_ID] = "TEST_ONLY_OTHER_OWNER"
        server = ThreadingHTTPServer(("127.0.0.1", 0), adm.Handler)
        server.daemon_threads = True
        adm.PORT = server.server_address[1]
        origin = f"http://127.0.0.1:{adm.PORT}"
        os.environ["BMT_ADMIN_PORT"] = str(adm.PORT)
        os.environ["BMT_ADMIN_PUBLIC_ORIGIN"] = origin
        ready = {"event": "READY", "role": role, "pid": os.getpid(), "host": "127.0.0.1",
                 "port": adm.PORT, "review_id": review_id, "property_id": property_id,
                 "admin_property_count": len(store.list_properties())}
    else:
        from customer_web_v2.sala_web import Application

        server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        app = Application(os.environ["SALA_DB_PATH"], owner_password=None, allowed_origins=set(),
                          public_projection_path=os.environ["SALA_PUBLIC_PROJECTION_PATH"],
                          public_assets_root=os.environ["SALA_PUBLIC_ASSETS_ROOT"])

        class CustomerHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                response = app.handle("GET", self.path, dict(self.headers), client_ip=self.client_address[0])
                self.send_response(response.status)
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response.body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                response = app.handle("POST", self.path, dict(self.headers), body, self.client_address[0])
                self.send_response(response.status)
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response.body)

            def log_message(self, fmt, *args):
                return

        server.RequestHandlerClass = CustomerHandler
        server.daemon_threads = True
        ready = {"event": "READY", "role": role, "pid": os.getpid(), "host": "127.0.0.1", "port": port}

    print(json.dumps(ready), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        if role == "admin":
            store.close()


def wait_ready(proc: subprocess.Popen[str]) -> dict[str, Any]:
    if proc.stdout is None:
        raise RuntimeError("CHILD_STDOUT_UNAVAILABLE")
    line = proc.stdout.readline()
    ready = json.loads(line)
    if ready.get("event") != "READY":
        raise RuntimeError("CHILD_READY_INVALID")
    return ready


def listener_map() -> dict[str, str]:
    result = subprocess.run(["lsof", "-nP", "-iTCP:8765", "-iTCP:18876", "-iTCP:18877", "-sTCP:LISTEN"],
                            capture_output=True, text=True, timeout=5, check=False)
    found = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 9:
            endpoint = next((part for part in parts if part.endswith((":8765", ":18876", ":18877"))), "")
            if endpoint:
                found[endpoint.rsplit(":", 1)[-1]] = parts[1]
    return found


def run() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    evidence: dict[str, Any] = {"source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    source_files = ("admin/admin_server.py", "customer_web_v2/sala_web.py", "customer_web_v2/projection_reader.py")
    evidence["source_sha256"] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_files}
    before = listener_map()
    temp = tempfile.TemporaryDirectory(prefix="sala-issue39-single-session-", dir="/private/tmp")
    root = Path(temp.name)
    root.chmod(0o700)
    (root / "tmp").mkdir(mode=0o700)
    for name in ("admin-private", "customer-private", "public-assets"):
        (root / name).mkdir(mode=0o700)
    password_file = root / "owner-password"
    password_file.write_text(TEST_PASSWORD, encoding="utf-8")
    password_file.chmod(0o600)
    admin = customer = None
    try:
        admin = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                 env=child_env(root, "admin"), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        admin_ready = wait_ready(admin)
        customer = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                    env=child_env(root, "customer"), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        customer_ready = wait_ready(customer)
        evidence["processes"] = {"admin": admin_ready, "customer": customer_ready}
        evidence["ports"] = [admin_ready["port"], customer_ready["port"]]
        evidence["root_mode"] = oct(root.stat().st_mode & 0o777)
        if admin_ready["port"] == customer_ready["port"] or any(p in {8765, 18876, 18877} for p in evidence["ports"]):
            raise AssertionError("LOOPBACK_PORT_ISOLATION_FAILED")
        admin_origin = f"http://127.0.0.1:{admin_ready['port']}"
        customer_origin = f"http://127.0.0.1:{customer_ready['port']}"
        admin_headers = {"Origin": admin_origin}
        review_id = admin_ready["review_id"]
        property_id = admin_ready["property_id"]
        expected_payload = {"property_id": property_id, "expected_review_revision": 1,
                            "expected_property_revision": 1,
                            "idempotency_key": "TEST_ONLY-single-session-publish-01"}

        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release", headers=admin_headers)
        record(evidence, "anonymous_admin_web_status_denied", 401, status)
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                            payload=expected_payload, headers=admin_headers)
        record(evidence, "anonymous_admin_publication_denied", 401, status)
        invalid_headers = {**admin_headers, "Cookie": "bmt_admin_session=TEST_ONLY-invalid-session"}
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release", headers=invalid_headers)
        record(evidence, "invalid_session_status_denied", 401, status)
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/revoke", method="POST",
                            payload={**expected_payload, "idempotency_key": "TEST_ONLY-invalid-session-revoke",
                                     "reason": "TEST_ONLY invalid session"}, headers=invalid_headers)
        record(evidence, "invalid_session_revoke_denied", 401, status)
        expired_headers = {**admin_headers, "Cookie": f"bmt_admin_session={EXPIRED_SESSION_ID}"}
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                            payload={**expected_payload, "idempotency_key": "TEST_ONLY-expired-session"}, headers=expired_headers)
        record(evidence, "expired_session_publication_denied", 401, status)
        evidence["expired_test_session_fixture"] = "pre-expired TEST_ONLY session entry; exercised through actual HTTP route"
        other_owner_headers = {**admin_headers, "Cookie": f"bmt_admin_session={OTHER_OWNER_SESSION_ID}"}
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                            payload={**expected_payload, "idempotency_key": "TEST_ONLY-other-owner-session"}, headers=other_owner_headers)
        record(evidence, "different_owner_session_publication_denied", 403, status)

        status, headers, login = call(admin_origin + "/api/auth/login", method="POST",
                                      payload={"password": TEST_PASSWORD}, headers=admin_headers)
        record(evidence, "single_admin_login", 200, status)
        if not isinstance(login, dict) or login.get("authenticated") is not True or login.get("owner_id") != "TEST_ONLY_OWNER":
            raise AssertionError("ADMIN_LOGIN_CONTRACT_MISMATCH")
        cookie = SimpleCookie()
        cookie.load(headers.get("Set-Cookie", ""))
        if "bmt_admin_session" not in cookie or not cookie["bmt_admin_session"].value:
            raise AssertionError("ADMIN_SESSION_COOKIE_MISSING")
        session_headers = {**admin_headers, "Cookie": f"bmt_admin_session={cookie['bmt_admin_session'].value}"}
        evidence["successful_admin_login_count"] = 1
        evidence["session_cookie_sent_to_customer"] = False
        status, _, admin_page = call(admin_origin + "/", headers=session_headers)
        record(evidence, "admin_web_module_page_same_session", 200, status)
        if not isinstance(admin_page, str) or "data-release-approve" not in admin_page or "data-release-revoke" not in admin_page:
            raise AssertionError("ADMIN_WEB_MODULE_UI_NOT_PRESENT")

        status, _, snapshot = call(admin_origin + f"/api/reviews/{review_id}/public-release", headers=session_headers)
        record(evidence, "same_session_web_module_read", 200, status)
        if snapshot.get("property_id") != property_id or snapshot.get("state") != "NOT_APPROVED":
            raise AssertionError("ADMIN_WEB_STATUS_LINEAGE_MISMATCH")
        status, _, _ = call(customer_origin + "/api/listings")
        record(evidence, "gate_a_without_web_publication_customer_hidden", 503, status)
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                            payload={**expected_payload, "owner_id": "TEST_ONLY_SPOOF"}, headers=session_headers)
        record(evidence, "owner_body_spoof_rejected", 403, status)
        csrf_headers = {"Origin": "https://invalid.example", "Cookie": session_headers["Cookie"]}
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                            payload=expected_payload, headers=csrf_headers)
        record(evidence, "cross_origin_publication_rejected", 403, status)
        status, _, publication = call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                      payload=expected_payload, headers=session_headers)
        if isinstance(publication, dict):
            evidence["publication_response_safe_fields"] = {"error": publication.get("error"),
                                                               "blockers": publication.get("blockers", [])}
        record(evidence, "same_session_web_publication", 201, status)
        if publication.get("published") is not False or publication.get("property_id") != property_id:
            raise AssertionError("WEB_PUBLICATION_CONTRACT_MISMATCH")
        evidence["web_publication"] = {"published": publication["published"], "property_id_matches": True,
                                       "same_admin_cookie_for_status_and_approve": True}

        status, _, feed = call(customer_origin + "/api/listings")
        record(evidence, "customer_public_feed_without_owner_login", 200, status)
        if not isinstance(feed, dict) or len(feed.get("items", [])) != 1:
            raise AssertionError("CUSTOMER_PROJECTION_NOT_VISIBLE")
        if feed["items"][0].get("property_id") != property_id:
            raise AssertionError("CUSTOMER_PROPERTY_LINEAGE_MISMATCH")
        status, _, search = call(customer_origin + "/api/listings?q=TEST_ONLY")
        record(evidence, "customer_public_search_without_owner_login", 200, status)
        if len(search.get("items", [])) != 1:
            raise AssertionError("CUSTOMER_SEARCH_REQUIRES_OWNER_OR_MISSED_PROJECTION")
        listing_id = feed["items"][0]["listing_id"]
        status, _, detail = call(customer_origin + f"/api/listings/{listing_id}")
        record(evidence, "customer_public_detail_without_owner_login", 200, status)
        if detail.get("property_id") != property_id:
            raise AssertionError("CUSTOMER_DETAIL_LINEAGE_MISMATCH")
        evidence["customer_login_requests"] = 0
        evidence["customer_request_cookie"] = "none"

        status, _, revoked = call(admin_origin + f"/api/reviews/{review_id}/public-release/revoke", method="POST",
                                  payload={**expected_payload, "idempotency_key": "TEST_ONLY-single-session-revoke",
                                           "reason": "TEST_ONLY verify same-session revoke"}, headers=session_headers)
        record(evidence, "same_session_web_publication_revoke", 200, status)
        if not isinstance(revoked, dict) or revoked.get("status") != "REVOKED":
            raise AssertionError("WEB_REVOKE_CONTRACT_MISMATCH")
        status, _, empty_feed = call(customer_origin + "/api/listings")
        record(evidence, "customer_feed_after_admin_revoke", 200, status)
        if empty_feed.get("items") != []:
            raise AssertionError("CUSTOMER_FEED_NOT_WITHDRAWN")

        status, _, revoked = call(admin_origin + "/api/auth/logout", method="POST", headers=session_headers)
        record(evidence, "admin_session_logout", 200, status)
        if revoked.get("authenticated") is not False:
            raise AssertionError("LOGOUT_RESPONSE_CONTRACT_MISMATCH")
        status, _, _ = call(admin_origin + f"/api/reviews/{review_id}/public-release/revoke", method="POST",
                            payload={**expected_payload, "idempotency_key": "TEST_ONLY-after-logout-revoke",
                                     "reason": "TEST_ONLY verify revoked session"}, headers=session_headers)
        record(evidence, "logout_revoked_session_rejected", 401, status)
        evidence["one_login_flow"] = "one successful Admin login; same Admin session read/approved Web publication; Customer public feed used no Owner login or Admin cookie"
        with sqlite3.connect(f"file:{root / 'admin-private' / 'property-core.sqlite3'}?mode=ro", uri=True) as db:
            after_property_count = db.execute("SELECT COUNT(*) FROM canonical_properties").fetchone()[0]
        if after_property_count != admin_ready["admin_property_count"]:
            raise AssertionError("WEB_PUBLICATION_CHANGED_PROPERTY_CORE_COUNT")
        with sqlite3.connect(f"file:{root / 'customer-private' / 'legacy-customer.sqlite3'}?mode=ro", uri=True) as db:
            legacy_listing_count = db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            customer_owner_count = db.execute("SELECT COUNT(*) FROM owner_auth").fetchone()[0]
        evidence["canonical_property_count_before_after_publication"] = [admin_ready["admin_property_count"], after_property_count]
        evidence["temp_customer_legacy_listing_rows"] = legacy_listing_count
        evidence["customer_owner_auth_rows"] = customer_owner_count
        evidence["customer_owner_credential_configured"] = False
        evidence["status"] = "PASS_TEST_ONLY"
    except Exception as exc:
        evidence["status"] = "PARTIAL"
        evidence["failed_edge"] = str(exc)
        raise
    finally:
        for proc in (customer, admin):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
        evidence["processes_stopped"] = {"admin": admin is None or admin.poll() is not None,
                                          "customer": customer is None or customer.poll() is not None}
        after = listener_map()
        evidence["existing_services_unchanged"] = bool(before) and before == after
        evidence["existing_listeners_before"] = before
        evidence["existing_listeners_after"] = after
        evidence["cleanup"] = "temp root removed"
        temp.cleanup()
        print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    if "--child" in sys.argv:
        child()
    else:
        run()
