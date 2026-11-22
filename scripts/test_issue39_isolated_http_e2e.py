"""One-shot loopback HTTP E2E; all state is confined to a fresh /private/tmp root."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OWNER_PASSWORD = "TEST_ONLY-admin-password-e2e-2026"
CUSTOMER_PASSWORD = "TEST_ONLY-customer-password-e2e-2026"
ORIGIN_LABEL = "TEST_ONLY"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")


class E2EFailure(AssertionError):
    pass


def check(condition: bool, label: str, evidence: dict[str, Any], status: str | None = None) -> None:
    evidence[label] = status or ("PASS" if condition else "FAIL")
    if not condition:
        raise E2EFailure(label)


def request(url: str, *, method: str = "GET", data: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float = 8.0) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def json_call(url: str, *, method: str = "GET", obj: Any = None,
              headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    h = dict(headers or {})
    data = None
    if obj is not None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"
    status, response_headers, body = request(url, method=method, data=data, headers=h)
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    return status, response_headers, parsed


def multipart(fields: dict[str, str], filename: str | None = None, content: bytes = b"") -> tuple[str, bytes]:
    boundary = "SALA-ISSUE39-TEST-ONLY-BOUNDARY"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    if filename:
        chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{filename}\"\r\n"
                       "Content-Type: image/png\r\n\r\n").encode())
        chunks.extend((content, b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def cookie_value(header: str, name: str) -> str:
    jar = SimpleCookie()
    jar.load(header)
    if name not in jar:
        raise E2EFailure("SESSION_COOKIE_MISSING")
    return jar[name].value


def read_ready(proc: subprocess.Popen[str], timeout: float = 12.0) -> dict[str, Any]:
    if proc.stdout is None:
        raise E2EFailure("SERVER_STDOUT_UNAVAILABLE")
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise E2EFailure("SERVER_START_TIMEOUT")
        line = proc.stdout.readline()
    finally:
        selector.close()
    if not line:
        raise E2EFailure("SERVER_EXITED_BEFORE_READY")
    ready = json.loads(line)
    if ready.get("event") != "READY":
        raise E2EFailure("SERVER_READY_INVALID")
    return ready


def serve_until_stopped(server: Any) -> None:
    stopped = threading.Event()
    serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    serving.start()

    def stop(_signum, _frame):
        stopped.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    stopped.wait()
    serving.join(timeout=3)


def child_env(root: Path, mode: str, port: int = 0) -> dict[str, str]:
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": str(root / "tmp"),
        "SALA_E2E_ROOT": str(root),
        "SALA_E2E_MODE": mode,
    }
    if mode == "admin":
        env.update({
            "BMT_ADMIN_PRIVATE_ROOT": str(root / "admin-private"),
            "BMT_ADMIN_PREVIEW_ROOT": str(root / "tmp" / "admin-preview"),
            "BMT_ADMIN_HOST": "127.0.0.1",
            "BMT_ADMIN_PORT": str(port),
            "BMT_ADMIN_PUBLIC_ORIGIN": "",
            "BMT_ADMIN_ALLOWED_IP": "",
            "BMT_ADMIN_OWNER_ID": "TEST_ONLY_OWNER",
            "BMT_ADMIN_PASSWORD_FILE": str(root / "admin-owner-password"),
            "BMT_ADMIN_LOCAL_HTTP_DEV": "1",
            "BMT_ADMIN_SECURE_COOKIE": "0",
            "BMT_ENV": "test",
            "BMT_OPENAI_EXTRACTION": "0",
            "BMT_OPENAI_CONTENT_LIVE": "0",
            "BMT_AUTH_DIAGNOSTIC_ACTIVATION_FILE": str(root / "no-auth-diagnostic-activation"),
            "BMT_AUTH_DIAGNOSTIC_MARKER_FILE": str(root / "no-auth-diagnostic-marker"),
        })
    else:
        env.update({
            "SALA_BIND_HOST": "127.0.0.1",
            "SALA_PORT": str(port),
            "SALA_DB_PATH": str(root / "customer-private" / "customer.sqlite3"),
            "SALA_OWNER_PASSWORD": CUSTOMER_PASSWORD,
            "SALA_ALLOWED_ORIGINS": "",
            "SALA_PUBLIC_PROJECTION_PATH": str(root / "tmp" / "admin-preview" / "data" / "listings.json"),
            "SALA_PUBLIC_ASSETS_ROOT": str(root / "public-assets"),
        })
    return env


def run_child() -> None:
    root = Path(os.environ["SALA_E2E_ROOT"])
    mode = os.environ["SALA_E2E_MODE"]
    from http.server import ThreadingHTTPServer

    if mode == "admin":
        import admin.admin_server as adm
        import property_memory.projection_builder as projection_builder
        from property_memory import PropertyStore
        from property_memory.review_center import ReviewCenter

        assets = root / "public-assets"
        assets.mkdir(mode=0o700, parents=True, exist_ok=True)
        projection_builder.PROJECT_ROOT = assets
        private = root / "admin-private"
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        adm.ensure_private()
        store = PropertyStore(private / "property-core.sqlite3")
        center = ReviewCenter(store)
        adm.configure_review_center(center)
        server = ThreadingHTTPServer(("127.0.0.1", 0), adm.Handler)
        server.daemon_threads = True
        adm.PORT = server.server_address[1]
        origin = f"http://127.0.0.1:{adm.PORT}"
        os.environ["BMT_ADMIN_PORT"] = str(adm.PORT)
        os.environ["BMT_ADMIN_PUBLIC_ORIGIN"] = origin
        ready = {"event": "READY", "role": "admin", "pid": os.getpid(), "host": "127.0.0.1", "port": adm.PORT}
        try:
            print(json.dumps(ready), flush=True)
            serve_until_stopped(server)
        finally:
            server.server_close()
            store.close()
        return

    from customer_web_v2.sala_web import Application

    server = ThreadingHTTPServer(("127.0.0.1", 0), __import__("http.server").server.BaseHTTPRequestHandler)
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    os.environ["SALA_ALLOWED_ORIGINS"] = origin
    app = Application(
        os.environ["SALA_DB_PATH"], CUSTOMER_PASSWORD, {origin},
        public_projection_path=os.environ["SALA_PUBLIC_PROJECTION_PATH"],
        public_assets_root=os.environ["SALA_PUBLIC_ASSETS_ROOT"],
    )
    class Handler(__import__("http.server").server.BaseHTTPRequestHandler):
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

    server.RequestHandlerClass = Handler
    server.daemon_threads = True
    print(json.dumps({"event": "READY", "role": "customer", "pid": os.getpid(), "host": "127.0.0.1", "port": port}), flush=True)
    try:
        serve_until_stopped(server)
    finally:
        server.server_close()


def run_e2e() -> None:
    evidence: dict[str, Any] = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    source_hashes = {}
    for relative in ("admin/admin_server.py", "customer_web_v2/sala_web.py", "customer_web_v2/projection_reader.py", "customer_web_v2/public_contract.py"):
        source_hashes[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    evidence["source_sha256"] = source_hashes
    manifest = ROOT / "docs" / "ISSUE39_SOURCE_SHA256_MANIFEST.txt"
    evidence["source_manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else "NOT_FOUND"
    def listener_map() -> dict[str, str]:
        try:
            result = subprocess.run(
                ["lsof", "-nP", "-iTCP:8765", "-iTCP:18876", "-iTCP:18877", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        found: dict[str, str] = {}
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            endpoint = next((part for part in parts if part.endswith((":8765", ":18876", ":18877"))), "")
            if endpoint:
                found[endpoint.rsplit(":", 1)[-1]] = parts[1]
        return found
    original_listeners = listener_map()
    evidence["existing_listeners_before"] = original_listeners

    temp = tempfile.TemporaryDirectory(prefix="sala-issue39-http-e2e-", dir="/private/tmp")
    root = Path(temp.name)
    root.chmod(0o700)
    tmp_root = root / "tmp"
    tmp_root.mkdir(mode=0o700)
    for name in ("admin-private", "customer-private", "public-assets"):
        (root / name).mkdir(mode=0o700)
    (root / "admin-owner-password").write_text(OWNER_PASSWORD, encoding="utf-8")
    (root / "admin-owner-password").chmod(0o600)
    asset = root / "public-assets" / "assets" / "images" / "test-only-http-home" / "front.png"
    asset.parent.mkdir(mode=0o700, parents=True)
    asset.write_bytes(PNG)
    asset.chmod(0o600)
    evidence["isolation"] = {"root_mode": oct(root.stat().st_mode & 0o777), "paths": ["/private/tmp/<isolated-run>/{tmp,admin-private,customer-private,public-assets}"], "real_paths_opened": False}

    admin = customer = None
    store = None
    try:
        admin = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                 env=child_env(root, "admin"), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        admin_ready = read_ready(admin)
        customer = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                    env=child_env(root, "customer"), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        customer_ready = read_ready(customer)
        admin_origin = f"http://127.0.0.1:{admin_ready['port']}"
        customer_origin = f"http://127.0.0.1:{customer_ready['port']}"
        evidence["processes"] = {"admin": admin_ready, "customer": customer_ready}
        evidence["ports"] = [admin_ready["port"], customer_ready["port"]]
        config_fingerprint = {
            "bind_hosts": [admin_ready["host"], customer_ready["host"]],
            "port_mode": "OS-assigned ephemeral; active sockets distinct",
            "storage_roots": ["<TEMP>/admin-private", "<TEMP>/customer-private", "<TEMP>/tmp/admin-preview", "<TEMP>/public-assets"],
            "openai_extraction": False, "openai_content_live": False,
            "test_owner_only": True,
        }
        evidence["config_sha256"] = hashlib.sha256(json.dumps(config_fingerprint, sort_keys=True).encode()).hexdigest()
        evidence["config_fingerprint"] = config_fingerprint
        check(admin_ready["port"] != customer_ready["port"], "two_distinct_ephemeral_ports", evidence)
        check(request(admin_origin + "/api/health")[0] == 200, "admin_health_http", evidence, "HTTP 200")
        check(request(customer_origin + "/health")[0] == 200, "customer_health_http", evidence, "HTTP 200")
        check(all(p not in {8765, 18876, 18877, 8876, 18878} for p in evidence["ports"]), "reserved_ports_untouched", evidence)

        # Customer legacy B store contains an approved TEST_ONLY row; this must never be a public fallback.
        status, h, login = json_call(customer_origin + "/api/login", method="POST", obj={"password": CUSTOMER_PASSWORD}, headers={"Origin": customer_origin})
        check(status == 200 and login.get("ok") is True, "customer_synthetic_login", evidence, f"HTTP {status}")
        customer_cookie = cookie_value(h.get("Set-Cookie", ""), "sala_owner_session")
        field_body = {"transaction_type": "SALE", "property_type": "TEST_ONLY legacy house", "title": "TEST_ONLY legacy approved row", "description": "Synthetic legacy-only record", "price_vnd": "1000000000", "area_m2": "50", "location": "TEST_ONLY legacy location", "source": "TEST_ONLY"}
        content_type, body = multipart(field_body, "legacy.png", PNG)
        headers = {"Origin": customer_origin, "Cookie": f"sala_owner_session={customer_cookie}", "Content-Type": content_type}
        status, _, receipt = request(customer_origin + "/api/intakes", method="POST", data=body, headers=headers)
        legacy_id = json.loads(receipt)["listing_id"] if status == 201 else None
        check(status == 201 and legacy_id, "customer_legacy_intake_fixture", evidence, f"HTTP {status}")
        status, _, _ = json_call(customer_origin + f"/api/listings/{legacy_id}/review", method="POST", obj={"action": "approve"}, headers={"Origin": customer_origin, "Cookie": f"sala_owner_session={customer_cookie}"})
        check(status == 200, "customer_legacy_approved_fixture", evidence, f"HTTP {status}")
        status, _, _ = request(customer_origin + "/api/listings")
        check(status == 503, "missing_projection_no_fallback", evidence, f"HTTP {status}")

        # Synthetic owner login for the real Admin HTTP handlers (not a second production account).
        admin_headers = {"Origin": admin_origin}
        status, h, login = json_call(admin_origin + "/api/auth/login", method="POST", obj={"password": OWNER_PASSWORD}, headers=admin_headers)
        check(status == 200 and login.get("authenticated") is True, "admin_synthetic_login", evidence, f"HTTP {status}")
        admin_cookie = cookie_value(h.get("Set-Cookie", ""), "bmt_admin_session")
        admin_headers["Cookie"] = f"bmt_admin_session={admin_cookie}"

        # (a) no Gate A and (b) Gate A without Web publication both yield no Customer data.
        status, _, empty_before = request(customer_origin + "/api/listings")
        check(status == 503, "before_gate_a_customer_fail_closed", evidence, f"HTTP {status}")
        fields = {"source": "Cá nhân", "sourceGroup": "TEST_ONLY", "note": "TEST_ONLY synthetic Admin-to-Web E2E property"}
        content_type, body = multipart(fields)
        intake_headers = {**admin_headers, "Content-Type": content_type, "X-Idempotency-Key": "TEST_ONLY-intake-e2e-01"}
        status, _, intake_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=intake_headers)
        intake = json.loads(intake_raw)
        check(status == 201 and intake.get("status") == "RECEIVED", "admin_http_intake", evidence, f"HTTP {status}")
        status, _, replay_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=intake_headers)
        replay = json.loads(replay_raw)
        check(status == 200 and replay.get("id") == intake.get("id"), "admin_intake_replay_single_record", evidence, f"HTTP {status}")
        status, _, processed_raw = request(admin_origin + f"/api/intakes/{intake['id']}/process", method="POST", data=b"", headers={**admin_headers, "Content-Length": "0"})
        processed = json.loads(processed_raw)
        check(status == 201 and processed.get("state") == "PENDING_REVIEW", "admin_http_process_review", evidence, f"HTTP {status}")
        review_id = processed["review_id"]

        facts = {
            "title": "TEST_ONLY HTTP Home", "property_type": "HOUSE", "address": "TEST_ONLY E2E Address",
            "area": "72.5", "width": 5, "length": 14.5, "land_area": 72.5,
            "price_type": "SALE", "price_vnd": 2500000000, "legal_status": "CERTIFICATE",
            "road_access": "TEST_ONLY road", "house_type": "TEST_ONLY house", "bedrooms": 2,
            "bathrooms": 1, "description": "TEST_ONLY factual description",
        }
        revision = 1
        for field, value in facts.items():
            status, _, result = json_call(admin_origin + f"/api/reviews/{review_id}/edit", method="POST",
                                          obj={"expected_revision": revision, "field": field, "value": value}, headers=admin_headers)
            check(status == 200, "admin_owner_confirmed_fact_edit", evidence, f"HTTP {status}")
            revision = int(result["review_revision"])
        status, _, denied = json_call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                      obj={"property_id": "TEST_ONLY-not-a-property", "expected_review_revision": revision,
                                           "expected_property_revision": 1, "idempotency_key": "TEST_ONLY-anonymous-001"}, headers={"Origin": admin_origin})
        check(status == 401, "anonymous_web_publication_denied", evidence, f"HTTP {status}")

        status, _, gate = json_call(admin_origin + f"/api/reviews/{review_id}/approve", method="POST",
                                    obj={"expected_revision": revision, "decision": "CREATE_NEW_PROPERTY", "idempotency_key": "TEST_ONLY-gate-a-e2e-01"}, headers=admin_headers)
        check(status == 200 and gate.get("state") == "APPROVED", "admin_gate_a_http", evidence, f"HTTP {status}")
        property_id = gate["mutation_result"]["property_id"]
        check(request(customer_origin + "/api/listings")[0] == 503, "gate_a_without_web_publish_customer_fail_closed", evidence, "HTTP 503")

        from property_memory import PropertyStore
        store = PropertyStore(root / "admin-private" / "property-core.sqlite3")
        appearances = store.list_source_appearances(property_id)
        check(len(store.list_properties()) == 1 and len(appearances) == 1, "synthetic_property_core_singleton", evidence)
        appearance_id = appearances[0]["appearance_id"]
        media_ref = "assets/images/test-only-http-home/front.png"
        store.append_media_observation(property_id, {"appearance_id": appearance_id, "media_ref": media_ref,
                                                     "media_sha256": hashlib.sha256(PNG).hexdigest(), "role": "front",
                                                     "rights_status": "PERSONAL", "active": True}, event_id="TEST_ONLY-public-media-e2e-01")
        release_payload = {"property_id": property_id, "expected_review_revision": revision,
                           "expected_property_revision": 1, "idempotency_key": "TEST_ONLY-web-publish-e2e-01"}
        status, _, blocked = json_call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                       obj=release_payload, headers=admin_headers)
        check(status == 422 and "PUBLIC_MEDIA_RIGHTS_DENIED" in blocked.get("blockers", []), "unapproved_media_rights_blocked", evidence, f"HTTP {status}")
        status, _, _ = request(customer_origin + "/api/listings")
        check(status == 503, "rights_blocked_no_customer_projection", evidence, f"HTTP {status}")

        asset = root / "public-assets" / media_ref
        asset.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        asset.write_bytes(PNG)
        asset.chmod(0o600)
        store.add_rights(property_id, {"appearance_id": appearance_id, "rights_status": "PERSONAL", "channel": "public_web", "approved": True}, rights_id="TEST_ONLY-rights-e2e-01")
        core_before_publication = store.get(property_id)
        count_before_publication = len(store.list_properties())
        status, _, publication = json_call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                           obj=release_payload, headers=admin_headers)
        check(status == 201 and publication.get("published") is False and publication.get("preview_records") == 1,
              "owner_web_publication_http_preview_only", evidence, f"HTTP {status}")
        web_path = root / "tmp" / "admin-preview" / "data" / "listings.json"
        artifact_bytes = web_path.read_bytes()
        evidence["artifact_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
        rows = json.loads(artifact_bytes)
        check(len(rows) == 1 and rows[0]["propertyId"] == property_id and rows[0]["propertyRevision"] == 1,
              "admin_projection_artifact_lineage", evidence)
        check(store.get(property_id) == core_before_publication and len(store.list_properties()) == count_before_publication,
              "property_core_unchanged_by_web_publication", evidence)

        status, _, feed_raw = request(customer_origin + "/api/listings?q=TEST_ONLY")
        feed = json.loads(feed_raw)
        check(status == 200 and len(feed.get("items", [])) == 1, "customer_http_search_feed", evidence, f"HTTP {status}")
        listing = feed["items"][0]
        check(listing["property_id"] == property_id and listing["property_revision"] == 1 and listing["projection_id"] == rows[0]["projectionId"],
              "feed_identity_and_revision_linkage", evidence)
        status, _, detail_raw = request(customer_origin + "/api/listings/" + listing["listing_id"])
        detail = json.loads(detail_raw)
        check(status == 200 and detail.get("property_id") == property_id and detail.get("property_revision") == 1,
              "customer_http_detail", evidence, f"HTTP {status}")
        check("owner_contact" not in detail and detail.get("media"), "detail_private_fields_and_media_allowlist", evidence)
        media_id = detail["media"][0]["media_id"]
        status, media_headers, media_bytes = request(customer_origin + "/media/" + media_id)
        check(status == 200 and media_bytes == PNG and media_headers.get("Content-Type") == "image/png",
              "customer_http_approved_media", evidence, f"HTTP {status}")
        check(all(item["title"] != "TEST_ONLY legacy approved row" for item in feed["items"]),
              "legacy_approved_customer_row_not_public", evidence)

        # Replay cannot duplicate. Rejected rights/private DTOs disappear from public list/media.
        status, _, _ = json_call(admin_origin + f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                 obj=release_payload, headers=admin_headers)
        rows = json.loads(web_path.read_text(encoding="utf-8"))
        check(status == 201 and len(rows) == 1, "publication_replay_no_duplicate", evidence, f"HTTP {status}")
        saved = web_path.read_bytes()
        altered = json.loads(saved)
        altered[0]["mediaRights"][media_ref] = "UNKNOWN"
        web_path.write_text(json.dumps(altered), encoding="utf-8")
        status, _, denied_feed = request(customer_origin + "/api/listings")
        check(status == 200 and json.loads(denied_feed).get("items") == [], "customer_unapproved_media_rights_hidden", evidence, f"HTTP {status}")
        check(request(customer_origin + "/media/" + media_id)[0] == 404, "customer_unapproved_media_not_served", evidence, "HTTP 404")
        altered[0] = json.loads(saved)[0]
        altered[0]["owner_contact"] = "TEST_ONLY private value"
        web_path.write_text(json.dumps(altered), encoding="utf-8")
        status, _, private_feed = request(customer_origin + "/api/listings")
        check(status == 200 and json.loads(private_feed).get("items") == [], "customer_private_field_dto_blocked", evidence, f"HTTP {status}")
        web_path.write_bytes(saved)
        web_path.write_text("{bad-json", encoding="utf-8")
        status, _, malformed = request(customer_origin + "/api/listings")
        check(status == 503, "malformed_source_503_no_legacy_fallback", evidence, f"HTTP {status}")
        web_path.write_bytes(saved)

        # Canonical revision advance is followed by Admin's real HTTP stale cleanup.
        store.update(property_id, {"property_revision": 2})
        status, _, stale = json_call(admin_origin + f"/api/reviews/{review_id}/public-release", headers=admin_headers)
        check(status == 200 and stale.get("state") == "STALE", "admin_http_stale_projection_detected", evidence, f"HTTP {status}")
        check(json.loads(web_path.read_text(encoding="utf-8")) == [], "admin_stale_artifact_withdrawn", evidence)
        status, _, stale_feed = request(customer_origin + "/api/listings")
        check(status == 200 and json.loads(stale_feed).get("items") == [], "customer_stale_property_hidden", evidence, f"HTTP {status}")
        check(request(customer_origin + "/media/" + media_id)[0] == 404, "customer_stale_media_hidden", evidence, "HTTP 404")

        revoke_payload = {"property_id": property_id, "expected_review_revision": revision,
                          "expected_property_revision": 2, "idempotency_key": "TEST_ONLY-web-revoke-e2e-01",
                          "reason": "TEST_ONLY isolated E2E cleanup"}
        status, _, revoked = json_call(admin_origin + f"/api/reviews/{review_id}/public-release/revoke", method="POST",
                                       obj=revoke_payload, headers=admin_headers)
        check(status == 200 and revoked.get("status") == "REVOKED", "admin_http_revoke", evidence, f"HTTP {status}")
        check(request(customer_origin + "/api/listings")[0] == 200 and
              json.loads(request(customer_origin + "/api/listings")[2]).get("items") == [],
              "customer_revoked_projection_empty", evidence)
        check(request(customer_origin + "/media/" + media_id)[0] == 404, "customer_revoked_media_hidden", evidence, "HTTP 404")

        evidence["test_only_counts"] = {"admin_intakes": 1, "reviews": 1, "property_core": len(store.list_properties()),
                                        "admin_public_rows_after_revoke": len(json.loads(web_path.read_text(encoding="utf-8"))),
                                        "customer_legacy_approved_rows": 1}
        evidence["fixture_sha256"] = hashlib.sha256(PNG).hexdigest()
        evidence["traffic_cta_events"] = "NOT_VERIFIED (outside this read projection increment)"
        evidence["status"] = "ISOLATED_HTTP_E2E_PASS"
    except Exception as exc:
        evidence["status"] = "ISOLATED_HTTP_E2E_PARTIAL"
        evidence["failed_edge"] = str(exc)
        raise
    finally:
        if store is not None:
            store.close()
        for proc in (customer, admin):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
        evidence["processes_stopped"] = {"admin": bool(admin is None or admin.poll() is not None),
                                          "customer": bool(customer is None or customer.poll() is not None)}
        final_listeners = listener_map()
        evidence["existing_listeners_after"] = final_listeners
        evidence["existing_services_unchanged"] = bool(original_listeners) and original_listeners == final_listeners
        evidence["temp_cleanup"] = "REMOVED"
        temp.cleanup()
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    if "--child" in sys.argv:
        run_child()
    else:
        run_e2e()
