"""One-shot loopback HTTP E2E; all state is confined to a fresh /private/tmp root."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OWNER_PASSWORD = "TEST_ONLY-admin-password-e2e-2026"
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


def staging_customer_namespace() -> dict[str, Any] | None:
    """Optionally exercise only the authenticated, isolated HTTPS test projection."""
    values = {key: os.environ.get(key, "").strip() for key in (
        "ISSUE39_STAGING_CUSTOMER_ORIGIN", "ISSUE39_STAGING_TEST_NAMESPACE_ROOT",
        "ISSUE39_STAGING_TEST_NAMESPACE_TOKEN_FILE")}
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise E2EFailure("STAGING_TEST_NAMESPACE_CONFIG_INCOMPLETE")
    parts = urlsplit(values["ISSUE39_STAGING_CUSTOMER_ORIGIN"])
    if (parts.scheme != "https" or parts.netloc != "web-staging.haisala.io.vn"
            or parts.path or parts.query or parts.fragment):
        raise E2EFailure("STAGING_TEST_ORIGIN_NOT_ALLOWLISTED")
    root_input = Path(values["ISSUE39_STAGING_TEST_NAMESPACE_ROOT"]).expanduser()
    token_input = Path(values["ISSUE39_STAGING_TEST_NAMESPACE_TOKEN_FILE"]).expanduser()
    if root_input.is_symlink() or token_input.is_symlink():
        raise E2EFailure("STAGING_TEST_PATH_SYMLINK_REJECTED")
    root = root_input.resolve(strict=True)
    token_file = token_input.resolve(strict=True)
    if (not root.is_dir() or root.stat().st_uid != os.getuid()
            or (root.stat().st_mode & 0o777) != 0o700):
        raise E2EFailure("STAGING_TEST_ROOT_NOT_PRIVATE")
    if token_file != root / "control" / "bearer-token":
        raise E2EFailure("STAGING_TEST_TOKEN_PATH_INVALID")
    info = token_file.stat()
    if not info.st_uid == os.getuid() or (info.st_mode & 0o777) != 0o600 or not token_file.is_file():
        raise E2EFailure("STAGING_TEST_TOKEN_NOT_PRIVATE")
    token = token_file.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise E2EFailure("STAGING_TEST_TOKEN_INVALID")
    for directory in (root / "projection", root / "assets", root / "control"):
        if not directory.is_dir() or directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise E2EFailure("STAGING_TEST_STORAGE_NOT_PRIVATE")

    def https_get(path: str, *, auth_token: str | None = token) -> tuple[int, bytes]:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise E2EFailure("STAGING_TEST_PATH_INVALID")
        url = values["ISSUE39_STAGING_CUSTOMER_ORIGIN"] + path
        config = f'url = "{url}"\nmax-time = 20\nsilent\nshow-error\n'
        if auth_token is not None:
            config += f'header = "Authorization: Bearer {auth_token}"\n'
        config += 'write-out = "\\n__ISSUE39_HTTP_CODE__:%{http_code}\\n"\n'
        completed = subprocess.run(["curl", "--config", "-", "--output", "-"],
                                   input=config.encode("ascii"), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if completed.returncode:
            raise E2EFailure("STAGING_TEST_HTTPS_REQUEST_FAILED")
        marker = b"\n__ISSUE39_HTTP_CODE__:"
        body, sep, tail = completed.stdout.rpartition(marker)
        if not sep or not tail.strip().isdigit():
            raise E2EFailure("STAGING_TEST_HTTPS_STATUS_MISSING")
        return int(tail.strip()), body

    return {"root": root, "projection": root / "projection" / "listings.json", "assets_dir": root / "assets",
            "asset": root / "assets" / "images" / "test-only-http-home" / "front.png",
            "https_get": https_get}


def publish_isolated_fixture_to_staging(namespace: dict[str, Any] | None, artifact: bytes,
                                        source_asset: Path, expected_property_id: str,
                                        expected_listing_id: str, expected_media_id: str,
                                        evidence: dict[str, Any]) -> None:
    if namespace is None:
        return
    projection = namespace["projection"]
    asset = namespace["asset"]
    if projection.exists() or asset.exists() or any(namespace["assets_dir"].rglob("*")):
        raise E2EFailure("STAGING_TEST_NAMESPACE_NOT_EMPTY")
    temp_projection = projection.with_name(".listings.json.issue39-tmp")
    temp_projection.write_bytes(artifact)
    temp_projection.chmod(0o600)
    os.replace(temp_projection, projection)
    asset.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source_asset, asset)
    asset.chmod(0o600)
    request = namespace["https_get"]
    prefix = "/__issue39_test__"
    unauth = request(prefix + "/api/listings", auth_token=None)[0]
    bad_auth = request(prefix + "/api/listings", auth_token="invalid-test-token")[0]
    check(unauth == 401 and bad_auth == 401, "staging_test_namespace_auth_denied", evidence,
          f"no-token HTTP {unauth}; bad-token HTTP {bad_auth}")
    status, health_raw = request(prefix + "/health")
    check(status == 200 and json.loads(health_raw).get("storage") == "isolated_test_projection",
          "staging_test_namespace_health", evidence, f"HTTP {status}")
    status, feed_raw = request(prefix + "/api/listings")
    feed = json.loads(feed_raw)
    check(status == 200 and len(feed.get("items", [])) == 1, "staging_https_customer_feed", evidence, f"HTTP {status}")
    listing = feed["items"][0]
    check(listing.get("property_id") == expected_property_id and listing.get("listing_id") == expected_listing_id,
          "staging_https_customer_identity", evidence)
    status, search_raw = request(prefix + "/api/listings?q=TEST_ONLY")
    check(status == 200 and len(json.loads(search_raw).get("items", [])) == 1,
          "staging_https_customer_search", evidence, f"HTTP {status}")
    status, detail_raw = request(prefix + "/api/listings/" + expected_listing_id)
    detail = json.loads(detail_raw)
    check(status == 200 and detail.get("property_id") == expected_property_id
          and detail.get("property_revision") == 1 and "owner_contact" not in detail,
          "staging_https_customer_detail_private_allowlist", evidence, f"HTTP {status}")
    status, media = request(prefix + "/media/" + expected_media_id)
    check(status == 200 and hashlib.sha256(media).hexdigest() == hashlib.sha256(PNG).hexdigest(),
          "staging_https_customer_approved_media", evidence, f"HTTP {status}")
    evidence["staging_customer_https"] = {
        "origin": "web-staging.haisala.io.vn", "feed": 200, "search": 200,
        "detail": 200, "media": 200, "no_auth": unauth, "invalid_auth": bad_auth,
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
    }


def revoke_isolated_fixture_from_staging(namespace: dict[str, Any] | None, listing_id: str,
                                         media_id: str, evidence: dict[str, Any]) -> None:
    if namespace is None:
        return
    namespace["projection"].write_text("[]", encoding="utf-8")
    namespace["projection"].chmod(0o600)
    request = namespace["https_get"]
    prefix = "/__issue39_test__"
    status, feed_raw = request(prefix + "/api/listings")
    check(status == 200 and json.loads(feed_raw).get("items") == [],
          "staging_https_customer_revoke_feed_empty", evidence, f"HTTP {status}")
    detail_status, _ = request(prefix + "/api/listings/" + listing_id)
    media_status, _ = request(prefix + "/media/" + media_id)
    check(detail_status == 404 and media_status == 404,
          "staging_https_customer_revoke_hides_detail_media", evidence,
          f"detail HTTP {detail_status}; media HTTP {media_status}")
    evidence["staging_customer_https"]["after_revoke"] = {"feed": status, "detail": detail_status, "media": media_status}


def multipart(fields: dict[str, str], filename: str | None = None, content: bytes = b"",
              files: list[tuple[str, bytes]] | None = None) -> tuple[str, bytes]:
    boundary = "SALA-ISSUE39-TEST-ONLY-BOUNDARY"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    upload_files = files if files is not None else ([(filename, content)] if filename else [])
    for upload_name, upload_content in upload_files:
        content_type = "text/plain" if Path(upload_name).suffix.lower() == ".txt" else "image/png"
        chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{upload_name}\"\r\n"
                       f"Content-Type: {content_type}\r\n\r\n").encode())
        chunks.extend((upload_content, b"\r\n"))
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


def child_env(root: Path, mode: str, port: int = 0, *, customer_port: int = 0, admin_url: str | None = None) -> dict[str, str]:
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
            "BMT_TEST_ONLY": "1",
            "BMT_OPENAI_EXTRACTION": "0",
            "BMT_OPENAI_CONTENT_LIVE": "0",
            "BMT_ANALYTICS_ALLOWED_ORIGINS": f"http://127.0.0.1:{customer_port}" if customer_port else "",
            "BMT_AUTH_DIAGNOSTIC_ACTIVATION_FILE": str(root / "no-auth-diagnostic-activation"),
            "BMT_AUTH_DIAGNOSTIC_MARKER_FILE": str(root / "no-auth-diagnostic-marker"),
        })
    else:
        env.update({
            "SALA_BIND_HOST": "127.0.0.1",
            "SALA_PORT": str(port),
            "SALA_DB_PATH": str(root / "customer-private" / "unused.sqlite3"),
            "SALA_ALLOWED_ORIGINS": "",
            "SALA_PUBLIC_PROJECTION_PATH": str(root / "tmp" / "admin-preview" / "data" / "listings.json"),
            "SALA_PUBLIC_ASSETS_ROOT": str(root / "public-assets"),
            "SALA_ADMIN_ANALYTICS_URL": admin_url or "",
            "SALA_ENV": "test",
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
        server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("BMT_ADMIN_PORT", "0"))), adm.Handler)
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

    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("SALA_PORT", "0"))), __import__("http.server").server.BaseHTTPRequestHandler)
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    os.environ["SALA_ALLOWED_ORIGINS"] = origin
    app = Application(
        os.environ["SALA_DB_PATH"], None, {origin},
        public_projection_path=os.environ["SALA_PUBLIC_PROJECTION_PATH"],
        public_assets_root=os.environ["SALA_PUBLIC_ASSETS_ROOT"],
        analytics_relay_url=os.environ.get("SALA_ADMIN_ANALYTICS_URL"),
        test_mode=os.environ.get("SALA_ENV") == "test",
        legacy_owner_enabled=False,
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
    # Running a script by filename places ``scripts/`` at sys.path[0]; add the
    # checkout root explicitly so the parent harness can inspect its isolated DB.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    evidence: dict[str, Any] = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    staging_namespace = staging_customer_namespace()
    evidence["staging_customer_namespace_enabled"] = staging_namespace is not None
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
    for name in ("admin-private", "public-assets"):
        (root / name).mkdir(mode=0o700)
    (root / "admin-owner-password").write_text(OWNER_PASSWORD, encoding="utf-8")
    (root / "admin-owner-password").chmod(0o600)
    asset = root / "public-assets" / "assets" / "images" / "test-only-http-home" / "front.png"
    asset.parent.mkdir(mode=0o700, parents=True)
    asset.write_bytes(PNG)
    asset.chmod(0o600)
    evidence["isolation"] = {"root_mode": oct(root.stat().st_mode & 0o777), "paths": ["/private/tmp/<isolated-run>/{tmp,admin-private,public-assets}"], "real_paths_opened": False}

    admin = customer = None
    store = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        customer_port = probe.getsockname()[1]
        probe.close()
        admin = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                 env=child_env(root, "admin", customer_port=customer_port), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        admin_ready = read_ready(admin)
        admin_origin = f"http://127.0.0.1:{admin_ready['port']}"
        customer = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"], cwd=ROOT,
                                    env=child_env(root, "customer", customer_port, customer_port=customer_port,
                                                  admin_url=admin_origin + "/api/analytics/events"), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        customer_ready = read_ready(customer)
        customer_origin = f"http://127.0.0.1:{customer_ready['port']}"
        evidence["processes"] = {"admin": admin_ready, "customer": customer_ready}
        evidence["ports"] = [admin_ready["port"], customer_ready["port"]]
        config_fingerprint = {
            "bind_hosts": [admin_ready["host"], customer_ready["host"]],
            "port_mode": "OS-assigned ephemeral; active sockets distinct",
            "storage_roots": ["<TEMP>/admin-private", "<TEMP>/tmp/admin-preview", "<TEMP>/public-assets"],
            "openai_extraction": False, "openai_content_live": False,
            "test_owner_only": True,
        }
        evidence["config_sha256"] = hashlib.sha256(json.dumps(config_fingerprint, sort_keys=True).encode()).hexdigest()
        evidence["config_fingerprint"] = config_fingerprint
        check(admin_ready["port"] != customer_ready["port"], "two_distinct_ephemeral_ports", evidence)
        check(request(admin_origin + "/api/health")[0] == 200, "admin_health_http", evidence, "HTTP 200")
        check(request(customer_origin + "/health")[0] == 200, "customer_health_http", evidence, "HTTP 200")
        check(all(p not in {8765, 18876, 18877, 8876, 18878} for p in evidence["ports"]), "reserved_ports_untouched", evidence)

        # Public Customer build has no second Owner or Property Master surface.
        status, _, health = json_call(customer_origin + "/health")
        check(status == 200 and health.get("storage") == "admin_projection", "customer_projection_only_health", evidence, f"HTTP {status}")
        status, _, _ = json_call(customer_origin + "/api/login", method="POST", obj={"password": "TEST_ONLY-not-used"}, headers={"Origin": customer_origin})
        check(status == 404, "customer_legacy_login_disabled", evidence, f"HTTP {status}")
        status, _, _ = request(customer_origin + "/api/admin/listings")
        check(status == 404, "customer_legacy_admin_disabled", evidence, f"HTTP {status}")
        content_type, body = multipart({"title": "TEST_ONLY must not write"}, "ignored.png", PNG)
        status, _, _ = request(customer_origin + "/api/intakes", method="POST", data=body,
                               headers={"Origin": customer_origin, "Content-Type": content_type})
        check(status == 404, "customer_legacy_intake_disabled", evidence, f"HTTP {status}")
        customer_db = root / "customer-private" / "unused.sqlite3"
        check(not customer_db.exists() and not customer_db.parent.exists(), "customer_store_not_created", evidence)

        status, _, denied_test_route = json_call(admin_origin + "/api/ai9/test-only", method="POST",
                                                 headers={"Origin": admin_origin, "Content-Length": "0"})
        check(status == 401, "anonymous_test_only_route_denied", evidence, f"HTTP {status}")

        # Synthetic owner login for the real Admin HTTP handlers (not a second production account).
        admin_headers = {"Origin": admin_origin}
        status, h, login = json_call(admin_origin + "/api/auth/login", method="POST", obj={"password": OWNER_PASSWORD}, headers=admin_headers)
        check(status == 200 and login.get("authenticated") is True, "admin_synthetic_login", evidence, f"HTTP {status}")
        admin_cookie = cookie_value(h.get("Set-Cookie", ""), "bmt_admin_session")
        admin_headers["Cookie"] = f"bmt_admin_session={admin_cookie}"

        status, _, owner_page = request(admin_origin + "/", headers=admin_headers)
        page_text = owner_page.decode("utf-8")
        check(status == 200 and all(anchor in page_text for anchor in ("#intake-section", "#property-search", "#review-section", "#owner-analytics"))
              and '<a href="#review-section">Web Publish</a>' in page_text
              and '<a href="#owner-analytics">Traffic</a>' in page_text,
              "owner_mobile_navigation_present", evidence, f"HTTP {status}")
        check("AI9 TEST ONLY" not in page_text and "Chạy AI9 TEST_ONLY" not in page_text and "local-only" not in page_text
              and "TẠO CONTENT" in page_text and "/create-content" in page_text,
              "one_touch_content_ui_no_ai9_test_control", evidence)
        status, _, test_receipt = json_call(admin_origin + "/api/ai9/test-only", method="POST", headers={**admin_headers, "Content-Length": "0"})
        check(status == 200 and test_receipt.get("published") is False and test_receipt.get("mutated_property_store") is False,
              "authenticated_test_only_route_and_cleanup", evidence, f"HTTP {status}")
        status, _, after_cleanup_intakes = json_call(admin_origin + "/api/intakes", headers=admin_headers)
        status_reviews, _, after_cleanup_reviews = json_call(admin_origin + "/api/reviews", headers=admin_headers)
        check(status == 200 and after_cleanup_intakes == [] and status_reviews == 200 and after_cleanup_reviews == [],
              "test_only_route_leaves_no_intake_or_review", evidence)

        # (a) no Gate A and (b) Gate A without Web publication both yield no Customer data.
        status, _, empty_before = request(customer_origin + "/api/listings")
        check(status == 503, "before_gate_a_customer_fail_closed", evidence, f"HTTP {status}")
        empty_type, empty_body = multipart({})
        status, _, empty_raw = request(admin_origin + "/api/intakes", method="POST", data=empty_body,
                                       headers={**admin_headers, "Content-Type": empty_type})
        check(status == 400, "empty_note_and_files_validation_unchanged", evidence, f"HTTP {status}")
        test_media_fields = {"source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY upload dedup fixture"}
        intake_files = [(f"IMG_{i}.jpg", PNG + bytes([i])) for i in range(1, 7)]
        intake_files.insert(1, ("IMG_3143-copy.webp", intake_files[0][1]))
        content_type, body = multipart(test_media_fields, files=intake_files)
        media_headers = {**admin_headers, "Content-Type": content_type, "X-Idempotency-Key": "TEST_ONLY-seven-one-duplicate"}
        status, _, batch_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=media_headers)
        batch = json.loads(batch_raw)
        check(status == 201 and len(batch.get("media", [])) == 6
              and batch.get("upload") == {"received": 7, "accepted": 6, "duplicate_count": 1,
                                           "duplicates": ["IMG_3143-copy.webp"]},
              "seven_uploads_one_exact_duplicate", evidence, f"HTTP {status}")
        status, _, batch_replay_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=media_headers)
        batch_replay = json.loads(batch_replay_raw)
        check(status == 200 and batch_replay.get("id") == batch.get("id")
              and batch_replay.get("upload", {}).get("accepted") == 6,
              "seven_upload_replay_idempotent", evidence, f"HTTP {status}")
        cross_type, cross_body = multipart({**test_media_fields, "note": "TEST_ONLY cross-intake duplicate"},
                                           files=[("new-name.jpg", intake_files[0][1])])
        status, _, cross_raw = request(admin_origin + "/api/intakes", method="POST", data=cross_body,
                                       headers={**admin_headers, "Content-Type": cross_type,
                                                "X-Idempotency-Key": "TEST_ONLY-cross-intake-hash-01"})
        check(status == 409 and json.loads(cross_raw).get("code") == "DUPLICATE_MEDIA",
              "existing_intake_hash_duplicate_still_conflicts", evidence, f"HTTP {status}")
        status, _, batch_cleanup = json_call(admin_origin + f"/api/intakes/{batch['id']}", method="DELETE", obj=None,
                                              headers={**admin_headers, "X-Test-Fixture": "TEST_ONLY"})
        check(status == 200 and batch_cleanup.get("status") == "REMOVED",
              "seven_upload_fixture_cleanup", evidence, f"HTTP {status}")
        nonimage_type, nonimage_body = multipart(test_media_fields, files=[("a.txt", b"TEST_ONLY-text-duplicate"),
                                                                             ("b.txt", b"TEST_ONLY-text-duplicate")])
        status, _, nonimage_raw = request(admin_origin + "/api/intakes", method="POST", data=nonimage_body,
                                           headers={**admin_headers, "Content-Type": nonimage_type,
                                                    "X-Idempotency-Key": "TEST_ONLY-nonimage-duplicate-01"})
        check(status == 409 and json.loads(nonimage_raw).get("code") == "DUPLICATE_MEDIA",
              "nonimage_duplicate_keeps_previous_conflict", evidence, f"HTTP {status}")
        exact_type, exact_body = multipart(test_media_fields, files=[("same.jpg", b"TEST_ONLY-exact-same"),
                                                                       ("same.jpg", b"TEST_ONLY-exact-same")])
        status, _, exact_raw = request(admin_origin + "/api/intakes", method="POST", data=exact_body,
                                       headers={**admin_headers, "Content-Type": exact_type,
                                                "X-Idempotency-Key": "TEST_ONLY-same-name-exact-01"})
        exact = json.loads(exact_raw)
        check(status == 201 and len(exact.get("media", [])) == 1 and exact.get("upload", {}).get("duplicate_count") == 1,
              "same_filename_exact_duplicate_keeps_first", evidence, f"HTTP {status}")
        status, _, exact_cleanup = json_call(admin_origin + f"/api/intakes/{exact['id']}", method="DELETE", obj=None,
                                              headers={**admin_headers, "X-Test-Fixture": "TEST_ONLY"})
        check(status == 200, "same_filename_exact_fixture_cleanup", evidence, f"HTTP {status}")
        intake_headers = {**admin_headers, "Content-Type": content_type, "X-Idempotency-Key": "TEST_ONLY-intake-e2e-01"}
        intake_fields = {"source": "Cá nhân", "sourceGroup": "TEST_ONLY", "note": "TEST_ONLY synthetic Admin-to-Web E2E property"}
        content_type, body = multipart(intake_fields)
        intake_headers["Content-Type"] = content_type
        status, _, intake_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=intake_headers)
        intake = json.loads(intake_raw)
        check(status == 201 and intake.get("status") == "RECEIVED" and intake.get("media") == []
              and intake.get("upload", {}).get("accepted") == 0,
              "admin_http_note_only_intake", evidence, f"HTTP {status}")
        status, _, disabled_content = json_call(admin_origin + f"/api/intakes/{intake['id']}/create-content",
                                                 method="POST", obj={"idempotency_key":"TEST_ONLY-disabled-content-01"},
                                                 headers=admin_headers)
        check(status == 403 and disabled_content.get("error") == "CONTENT_PHASE_DISABLED",
              "staging_content_provider_remains_disabled", evidence, f"HTTP {status}")
        status, _, replay_raw = request(admin_origin + "/api/intakes", method="POST", data=body, headers=intake_headers)
        replay = json.loads(replay_raw)
        check(status == 200 and replay.get("id") == intake.get("id") and replay.get("upload", {}).get("accepted") == 0
              and replay.get("upload", {}).get("duplicate_count") == 0,
              "admin_intake_replay_single_record", evidence, f"HTTP {status}")
        status, _, denied_process = request(admin_origin + f"/api/intakes/{intake['id']}/process", method="POST",
                                            data=b"", headers={"Origin": admin_origin, "Content-Length": "0"})
        check(status == 401, "anonymous_intake_process_denied", evidence, f"HTTP {status}")
        status, _, missing_process = json_call(admin_origin + "/api/intakes/intake-TEST_ONLY-missing/process",
                                               method="POST", obj=None,
                                               headers={**admin_headers, "Content-Length": "0"})
        check(status == 404, "missing_intake_process_returns_404", evidence, f"HTTP {status}")
        collision_files = [("same-name.jpg", PNG + b"first"), ("same-name.jpg", PNG + b"second")]
        collision_type, collision_body = multipart({**test_media_fields, "note": "TEST_ONLY same filename distinct bytes"}, files=collision_files)
        status, _, collision_raw = request(admin_origin + "/api/intakes", method="POST", data=collision_body,
                                            headers={**admin_headers, "Content-Type": collision_type,
                                                     "X-Idempotency-Key": "TEST_ONLY-same-name-distinct-01"})
        collision = json.loads(collision_raw)
        check(status == 201 and len(collision.get("media", [])) == 2
              and len({x.get("storedFilename") for x in collision.get("media", [])}) == 2,
              "same_filename_distinct_bytes_preserved", evidence, f"HTTP {status}")
        for item, expected in zip(collision["media"], (collision_files[0][1], collision_files[1][1])):
            check((root / "admin-private" / item["path"]).read_bytes() == expected,
                  "same_filename_media_bytes_not_overwritten", evidence)
        status, _, collision_cleanup = json_call(admin_origin + f"/api/intakes/{collision['id']}", method="DELETE",
                                                  obj=None, headers={**admin_headers, "X-Test-Fixture": "TEST_ONLY"})
        check(status == 200 and collision_cleanup.get("status") == "REMOVED",
              "same_filename_fixture_cleanup", evidence, f"HTTP {status}")
        status, _, processed_raw = request(admin_origin + f"/api/intakes/{intake['id']}/process", method="POST", data=b"", headers={**admin_headers, "Content-Length": "0"})
        processed = json.loads(processed_raw)
        check(status == 201 and processed.get("state") == "PENDING_REVIEW", "admin_http_process_review", evidence, f"HTTP {status}")
        review_id = processed["review_id"]
        status, _, pending_before_replay = json_call(admin_origin + f"/api/reviews/{review_id}", headers=admin_headers)
        status_replay, _, processed_replay = request(admin_origin + f"/api/intakes/{intake['id']}/process", method="POST",
                                                     data=b"", headers={**admin_headers, "Content-Length": "0"})
        processed_replay = json.loads(processed_replay)
        status_after, _, pending_after_replay = json_call(admin_origin + f"/api/reviews/{review_id}", headers=admin_headers)
        status_list, _, review_rows = json_call(admin_origin + "/api/reviews", headers=admin_headers)
        status_properties, _, properties_before_gate = json_call(admin_origin + "/api/properties", headers=admin_headers)
        check(status == 200 and status_replay == 201 and status_after == 200 and status_list == 200
              and processed_replay.get("review_id") == review_id
              and processed_replay.get("state") == pending_before_replay.get("state") == pending_after_replay.get("state") == "PENDING_REVIEW"
              and processed_replay.get("review_revision") == pending_before_replay.get("review_revision") == pending_after_replay.get("review_revision") == 1
              and len([x for x in review_rows if x.get("intake_id") == intake["id"]]) == 1
              and status_properties == 200 and properties_before_gate == [],
              "process_replay_single_pending_review_no_core_write", evidence, f"HTTP {status_replay}")

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
        status, _, approved_before_process_replay = json_call(admin_origin + f"/api/reviews/{review_id}", headers=admin_headers)
        status_process_replay, _, approved_process_raw = request(admin_origin + f"/api/intakes/{intake['id']}/process", method="POST",
                                                                 data=b"", headers={**admin_headers, "Content-Length": "0"})
        approved_process = json.loads(approved_process_raw)
        status, _, approved_after_process_replay = json_call(admin_origin + f"/api/reviews/{review_id}", headers=admin_headers)
        check(status == 200 and status_process_replay == 201 and approved_process.get("review_id") == review_id
              and approved_process.get("state") == "APPROVED"
              and approved_process.get("review_revision") == approved_before_process_replay.get("review_revision") == approved_after_process_replay.get("review_revision")
              and approved_before_process_replay.get("state") == approved_after_process_replay.get("state") == "APPROVED"
              and approved_before_process_replay.get("decision") == approved_after_process_replay.get("decision")
              and approved_before_process_replay.get("mutation_result") == approved_after_process_replay.get("mutation_result") == gate.get("mutation_result")
              and approved_before_process_replay.get("owner_edits") == approved_after_process_replay.get("owner_edits"),
              "process_replay_approved_review_not_reset", evidence, f"HTTP {status_process_replay}")
        check(request(customer_origin + "/api/listings")[0] == 503, "gate_a_without_web_publish_customer_fail_closed", evidence, "HTTP 503")

        from property_memory import PropertyStore
        from property_memory.review_center import ReviewCenter
        store = PropertyStore(root / "admin-private" / "property-core.sqlite3")
        gate_b_fixture = ReviewCenter(store)
        approved_package = gate_b_fixture.view(review_id)
        check(not approved_package.get("selected_content"), "web_publish_without_gate_b_content", evidence)
        evidence["gate_b"] = "No Content variant fixture; Content UI/API remains dormant"

        status, _, content_denied = json_call(admin_origin + f"/api/reviews/{review_id}/content/create", method="POST", obj={}, headers=admin_headers)
        check(status == 404 and content_denied.get("error") == "CONTENT_PHASE_DISABLED",
              "content_phase_stays_dormant_without_ai_or_offline_generation", evidence, f"HTTP {status}")

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
              "owner_web_publication_http_preview_only", evidence, f"HTTP {status} {publication.get('error', '')} {publication.get('blockers', [])}")
        web_path = root / "tmp" / "admin-preview" / "data" / "listings.json"
        artifact_bytes = web_path.read_bytes()
        evidence["artifact_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
        rows = json.loads(artifact_bytes)
        check(len(rows) == 1 and rows[0]["propertyId"] == property_id and rows[0]["propertyRevision"] == 1,
              "admin_projection_artifact_lineage", evidence)
        canonical_property = store.get(property_id)
        check(rows[0].get("title") == canonical_property["title"]
              and bool(rows[0].get("description"))
              and not any(key in rows[0] for key in ("contentBatchId", "contentVariantId", "contentRevision")),
              "projection_uses_approved_property_facts_without_gate_b", evidence)
        check(store.get(property_id) == core_before_publication and len(store.list_properties()) == count_before_publication,
              "property_core_unchanged_by_web_publication", evidence)

        status, _, feed_raw = request(customer_origin + "/api/listings")
        feed = json.loads(feed_raw)
        check(status == 200 and len(feed.get("items", [])) == 1, "customer_http_feed", evidence, f"HTTP {status}")
        status, _, search_raw = request(customer_origin + "/api/listings?q=TEST_ONLY")
        search = json.loads(search_raw)
        check(status == 200 and len(search.get("items", [])) == 1, "customer_http_search", evidence, f"HTTP {status}")
        listing = feed["items"][0]
        check(listing["property_id"] == property_id and listing["property_revision"] == 1 and listing["projection_id"] == rows[0]["projectionId"],
              "feed_identity_and_revision_linkage", evidence)
        check(listing.get("title") == canonical_property["title"] and listing.get("description") == rows[0]["description"],
              "customer_reads_approved_property_facts_without_gate_b", evidence)
        publication_id = listing["publication_id"]
        check(publication_id == rows[0]["publicationId"], "feed_publication_id_linkage", evidence)
        status, _, detail_raw = request(customer_origin + "/api/listings/" + listing["listing_id"])
        detail = json.loads(detail_raw)
        check(status == 200 and detail.get("property_id") == property_id and detail.get("property_revision") == 1 and detail.get("publication_id") == publication_id,
              "customer_http_detail", evidence, f"HTTP {status}")
        check("owner_contact" not in detail and detail.get("media"), "detail_private_fields_and_media_allowlist", evidence)
        media_id = detail["media"][0]["media_id"]
        status, media_headers, media_bytes = request(customer_origin + "/media/" + media_id)
        check(status == 200 and media_bytes == PNG and media_headers.get("Content-Type") == "image/png",
              "customer_http_approved_media", evidence, f"HTTP {status}")
        check(not customer_db.exists(), "customer_legacy_store_absent_during_public_reads", evidence)
        publish_isolated_fixture_to_staging(staging_namespace, artifact_bytes, asset, property_id,
                                            listing["listing_id"], media_id, evidence)

        # Real HTTP Customer -> relay -> Admin analytics, all events bound to the active publication.
        traffic_events = [
            {"event_id": str(uuid.uuid4()), "event_type": "page_view", "page_type": "listing_detail",
             "property_id": property_id, "publication_id": publication_id, "utm_source": "qr", "utm_medium": "referral"},
            {"event_id": str(uuid.uuid4()), "event_type": "property_detail_view",
             "property_id": property_id, "publication_id": publication_id},
        ]
        for event in traffic_events:
            status, _, receipt = json_call(customer_origin + "/api/analytics/events", method="POST", obj=event,
                                           headers={"Origin": customer_origin})
            check(status == 202 and receipt == {"accepted": True, "duplicate": False},
                  "customer_http_event_receipt", evidence, f"HTTP {status}")
            status, _, replay = json_call(customer_origin + "/api/analytics/events", method="POST", obj=event,
                                          headers={"Origin": customer_origin})
            check(status == 200 and replay == {"accepted": True, "duplicate": True},
                  "customer_http_event_idempotent_replay", evidence, f"HTTP {status}")
        invalid_event = {**traffic_events[1], "event_id": str(uuid.uuid4()), "publication_id": "publication-00000000000000000000000000000000"}
        status, _, _ = json_call(customer_origin + "/api/analytics/events", method="POST", obj=invalid_event,
                                 headers={"Origin": customer_origin})
        check(status == 422, "customer_http_stale_publication_event_denied", evidence, f"HTTP {status}")
        private_event = {**traffic_events[1], "event_id": str(uuid.uuid4()), "phone": "0901234567"}
        status, _, _ = json_call(customer_origin + "/api/analytics/events", method="POST", obj=private_event,
                                 headers={"Origin": customer_origin})
        check(status == 422, "customer_http_private_event_field_denied", evidence, f"HTTP {status}")
        report_day = datetime.now(timezone.utc).date().isoformat()
        status, _, event_report = json_call(admin_origin + f"/api/analytics/report?from={report_day}&to={report_day}&property_id={property_id}", headers=admin_headers)
        check(status == 200 and event_report.get("total_events") == 2, "admin_http_owner_event_report", evidence, f"HTTP {status}")

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
        revoke_isolated_fixture_from_staging(staging_namespace, listing["listing_id"], media_id, evidence)
        status, _, revoked_event = json_call(customer_origin + "/api/analytics/events", method="POST",
                                              obj={**traffic_events[1], "event_id": str(uuid.uuid4())},
                                              headers={"Origin": customer_origin})
        check(status == 422, "customer_http_revoked_publication_event_denied", evidence, f"HTTP {status}")

        evidence["test_only_counts"] = {"admin_intakes": 1, "reviews": 1, "property_core": len(store.list_properties()),
                                        "admin_public_rows_after_revoke": len(json.loads(web_path.read_text(encoding="utf-8"))),
                                        "customer_legacy_property_rows": 0}
        evidence["fixture_sha256"] = hashlib.sha256(PNG).hexdigest()
        evidence["customer_contact"] = "NOT_CONFIGURED; disabled CTA emits no contact event"
        evidence["traffic_events"] = {"status": "PASS_HTTP", "accepted": 2, "replays": 2, "after_revoke": 422}
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
