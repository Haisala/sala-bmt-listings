"""Isolated HTTP regression proof for the Admin browser login/CSRF flow."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from email.message import Message
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "TEST_ONLY-login-csrf-password-2026"
QUERY_MARKER = "TEST_ONLY-QUERY-MUST-NOT-BE-LOGGED"


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def setup_server(root: Path):
    import admin.admin_server as adm
    from property_memory import PropertyStore
    from property_memory.review_center import ReviewCenter

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("admin-private", "preview", "staging", "public-assets"):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    password_file = root / "owner-password"
    password_file.write_text(TEST_PASSWORD + "\n", encoding="utf-8")
    password_file.chmod(0o600)
    store = PropertyStore(root / "admin-private" / "property-core.sqlite3")
    adm.ensure_private()
    adm.configure_review_center(ReviewCenter(store))
    server = ThreadingHTTPServer(("127.0.0.1", 0), adm.Handler)
    server.daemon_threads = True
    adm.PORT = server.server_address[1]
    os.environ["BMT_ADMIN_PORT"] = str(adm.PORT)
    os.environ["BMT_ADMIN_PUBLIC_ORIGIN"] = f"http://127.0.0.1:{adm.PORT}"
    check(server.server_address[0] == "127.0.0.1", "server_bind_loopback")
    return adm, store, server, os.environ["BMT_ADMIN_PUBLIC_ORIGIN"]


def request(port: int, method: str, path: str, *, body: bytes | None = None,
            headers: dict[str, str] | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        return response.status, response.getheaders(), payload
    finally:
        conn.close()


def header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key.lower() == name.lower()]


def cookie_from(headers: list[tuple[str, str]], name: str) -> str:
    for value in header_values(headers, "Set-Cookie"):
        parsed = SimpleCookie(); parsed.load(value)
        if name in parsed:
            return parsed[name].value
    return ""


def form_token(payload: bytes) -> str:
    match = re.search(rb'<input type="hidden" name="csrf_token" value="([A-Za-z0-9_-]+)">', payload)
    return match.group(1).decode("ascii") if match else ""


def get_form(port: int, headers: dict[str, str] | None = None):
    status, response_headers, body = request(port, "GET", "/login", headers=headers)
    return status, response_headers, body, cookie_from(response_headers, "bmt_login_pre_session"), form_token(body)


def post_form(port: int, password: str, token: str = "", *, cookie: str = "",
              origin: str | None = None, referer: str | None = None,
              fetch_site: str | None = None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = "bmt_login_pre_session=" + cookie
    if origin is not None:
        headers["Origin"] = origin
    if referer is not None:
        headers["Referer"] = referer
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    body = urlencode({"password": password, "csrf_token": token}).encode("ascii")
    return request(port, "POST", "/login", body=body, headers=headers)


def child(root: Path) -> int:
    adm, store, server, origin = setup_server(root)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence: list[str] = []
    try:
        status, headers, page, cookie, token = get_form(port)
        check(status == 200 and cookie and token, "get_login_form")
        check(b"strict-origin-when-cross-origin" in page, "login_form_referrer_meta")
        check(header_values(headers, "Cache-Control") == ["no-store"], "login_no_store")
        check(header_values(headers, "Referrer-Policy") == ["strict-origin-when-cross-origin"], "login_referrer_header")
        set_cookie = header_values(headers, "Set-Cookie")[0]
        check("HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie and "Path=/" in set_cookie and "Domain=" not in set_cookie, "pre_session_cookie_scope")
        evidence.append("GET /login 200; no-store; strict referrer policy; host-only HttpOnly SameSite=Strict pre-session cookie")
        status, _, body = request(port, "GET", "/login?next=" + QUERY_MARKER)
        check(status == 200 and form_token(body), "query_bearing_login_target")

        # A query-bearing same-origin Referer is valid; URL query never enters logs.
        status, h, body = post_form(port, TEST_PASSWORD, token, cookie=cookie, origin=origin,
                                    referer=origin + "/login?marker=" + QUERY_MARKER)
        check(status == 303 and header_values(h, "Location") == ["/"], "same_origin_query_referer_login")
        session_cookie = cookie_from(h, "bmt_admin_session")
        check(session_cookie and session_cookie != cookie, "fresh_authenticated_session")
        check("Secure" not in header_values(h, "Set-Cookie")[0], "test_http_cookie_not_secure")
        check(any("bmt_login_pre_session=" in value and "Max-Age=0" in value for value in header_values(h, "Set-Cookie")), "pre_session_cleared")
        status, _, body = request(port, "GET", "/", headers={"Cookie": "bmt_admin_session=" + session_cookie})
        check(status == 200, "authenticated_home")
        evidence.append("same-origin POST with query Referer → HTTP 303; new session; authenticated Home HTTP 200")

        # Replayed single-use token returns a recoverable HTML form, never a JSON trap.
        status, h, body = post_form(port, TEST_PASSWORD, token, cookie=cookie, origin=origin)
        check(status == 200 and b"Phi\xc3\xaAn \xc4\x91\xc4\x83ng nh\xe1\xba\xadp" in body and form_token(body), "replayed_token_friendly_recovery")
        evidence.append("replayed login token rejected; HTTP 200 fresh Vietnamese form")

        # Browser variants permitted only through an independent valid pre-session token.
        for case, origin_header, referer, expected in (
            ("referer_without_origin", None, origin + "/login?from=" + QUERY_MARKER, 303),
            ("neither_header", None, None, 303),
            ("opaque_null_origin", "null", None, 303),
        ):
            _, _, _, c, t = get_form(port)
            status, _, _ = post_form(port, TEST_PASSWORD, t, cookie=c, origin=origin_header, referer=referer)
            check(status == expected, case)
            evidence.append(case + " valid pre-session token → HTTP 303")
        _, _, _, c, t = get_form(port)
        status, _, _ = post_form(port, TEST_PASSWORD, t, cookie=c, origin=origin,
                                 fetch_site="same-origin")
        check(status == 303, "fetch_metadata_same_origin_allowed")
        evidence.append("Sec-Fetch-Site: same-origin with valid token → HTTP 303")
        _, _, _, c, t = get_form(port)
        status, _, body = post_form(port, TEST_PASSWORD, t, cookie=c, origin=origin,
                                    fetch_site="cross-site")
        check(status == 403 and b"<form" in body, "fetch_metadata_cross_site_rejected")
        evidence.append("Sec-Fetch-Site: cross-site rejected with valid token → HTTP 403")

        # Wrong credentials consume the token, refresh the form, and can recover.
        _, _, _, c, t = get_form(port)
        status, h, body = post_form(port, "TEST_ONLY-wrong-password", t, cookie=c, origin=origin)
        fresh = form_token(body)
        check(status == 200 and fresh and fresh != t and b"M\xe1\xba\xadt kh\xe1\xba\xa9u kh\xc3\xb4ng \xc4\x91\xc3\xbang" in body, "wrong_password_recovery")
        c2 = cookie_from(h, "bmt_login_pre_session")
        check(c2 == c, "wrong_password_keeps_preauth_session")
        status, h, _ = post_form(port, TEST_PASSWORD, fresh, cookie=c2, origin=origin)
        check(status == 303 and cookie_from(h, "bmt_admin_session"), "correct_after_wrong_password")
        evidence.append("wrong password → fresh single-use form; subsequent correct synthetic credential → HTTP 303")

        # Missing, invalid, and cross-session tokens fail before password verification.
        password_calls: list[bool] = []
        original_password_ok = adm.password_ok
        adm.password_ok = lambda value: (password_calls.append(True), original_password_ok(value))[1]
        try:
            _, _, _, ca, _ = get_form(port)
            before = len(password_calls)
            status, h, body = post_form(port, TEST_PASSWORD, "", cookie=ca, origin=origin)
            check(status == 200 and form_token(body) and len(password_calls) == before, "missing_token_before_password")
            _, _, _, ca, ta = get_form(port)
            _, _, _, cb, tb = get_form(port)
            before = len(password_calls)
            status, _, body = post_form(port, TEST_PASSWORD, tb, cookie=ca, origin=origin)
            check(status == 200 and form_token(body) and len(password_calls) == before and ca != cb, "cross_session_token_rejected")
        finally:
            adm.password_ok = original_password_ok
        evidence.append("missing and cross-session token rejected before password verification")

        # Foreign, malformed, and conflicting sources are rejected even with a valid token.
        for case, o, ref in (
            ("foreign_origin", "https://evil.example", None),
            ("malformed_origin", "null/evil", None),
            ("conflicting_referer", origin, "https://evil.example/login?marker=" + QUERY_MARKER),
            ("foreign_referer_without_origin", None, "https://evil.example/login"),
        ):
            _, _, _, c, t = get_form(port)
            status, _, body = post_form(port, TEST_PASSWORD, t, cookie=c, origin=o, referer=ref)
            check(status == 403 and b"Kh\xc3\xb4ng th\xe1\xbb\x83 x\xc3\xa1c minh ngu\xe1\xbb\x93n g\xe1\xbb\xad" in body and b"<form" in body, case)
            evidence.append(case + " → HTTP 403; friendly fresh HTML form")

        # API login remains strict-origin; authenticated logout remains CSRF-protected.
        api_body = json.dumps({"password": TEST_PASSWORD}).encode()
        status, _, _ = request(port, "POST", "/api/auth/login", body=api_body, headers={"Content-Type": "application/json"})
        check(status == 403, "api_login_missing_origin_rejected")
        status, h, _ = request(port, "POST", "/api/auth/login", body=api_body,
                               headers={"Content-Type": "application/json", "Origin": origin})
        check(status == 200, "api_login_same_origin")
        api_session = cookie_from(h, "bmt_admin_session")
        status, _, _ = request(port, "POST", "/api/auth/logout", body=b"{}",
                               headers={"Content-Type": "application/json", "Origin": "https://evil.example", "Cookie": "bmt_admin_session=" + api_session})
        check(status == 403, "authenticated_logout_foreign_origin_rejected")
        status, _, _ = request(port, "POST", "/api/auth/logout", body=b"{}",
                               headers={"Content-Type": "application/json", "Origin": origin,
                                        "Referer": origin + "/login?marker=" + QUERY_MARKER,
                                        "Cookie": "bmt_admin_session=" + api_session})
        check(status == 200, "authenticated_logout_same_origin")
        status, _, _ = request(port, "POST", "/api/admin/credential-preflight", body=b"{}",
                               headers={"Content-Type": "application/json", "Origin": origin})
        check(status == 401, "unauthenticated_owner_action_denied")
        evidence.append("API login strict-origin; authenticated logout foreign origin 403 / same-origin 200; unauthenticated Owner action 401")

        # Store expiration, restart invalidation, bounded storage, and Secure cookie syntax.
        temporary = adm.LoginPreSessionStore(ttl_seconds=2)
        exp_cookie, exp_token = temporary.issue(now=100)
        check(temporary.consume(exp_cookie, exp_token, now=103) == "EXPIRED", "token_expiry")
        restart_cookie, restart_token = temporary.issue(now=100)
        check(adm.LoginPreSessionStore(ttl_seconds=2).consume(restart_cookie, restart_token, now=101) == "INVALID", "restart_invalidates_presession")
        for index in range(adm.LoginPreSessionStore.MAX_SESSIONS + 8):
            temporary.issue(now=200 + index)
        check(len(temporary._sessions) <= temporary.MAX_SESSIONS, "bounded_preauth_store")
        adm.SECURE_COOKIE = True
        secure_cookie = adm.Handler.login_pre_session_cookie(None, "TEST_ONLY-cookie", 60)
        secure_session_cookie = adm.Handler.session_cookie(None, "TEST_ONLY-session", 60)
        adm.SECURE_COOKIE = False
        check("Secure" in secure_cookie and "HttpOnly" in secure_cookie and "SameSite=Strict" in secure_cookie and "Domain=" not in secure_cookie, "secure_cookie_syntax")
        check("Secure" in secure_session_cookie and "HttpOnly" in secure_session_cookie and "SameSite=Strict" in secure_session_cookie and "Domain=" not in secure_session_cookie, "secure_session_cookie_syntax")
        duplicated = Message(); duplicated.add_header("Origin", origin); duplicated.add_header("Origin", "https://evil.example")
        accepted, reason, _ = adm.login_header_diagnostics(duplicated)
        check(not accepted and reason == "SOURCE_CONFLICT", "duplicate_origin_rejected")
        evidence.append("token expiry/restart invalidation/bounded store and Secure host-only cookie syntax PASS")

        # Sanitized login telemetry is bounded and contains no submitted credentials or URL query.
        diag = root / "admin-private" / "auth-correlation.jsonl"
        check(diag.is_file() and (diag.stat().st_mode & 0o777) == 0o600, "diagnostic_file_private")
        text = diag.read_text(encoding="utf-8")
        check(TEST_PASSWORD not in text and QUERY_MARKER not in text and token not in text and cookie not in text, "diagnostics_secret_free")
        rows = [json.loads(line) for line in text.splitlines() if line]
        check(len(rows) <= 100 and all(row.get("path") in {"/login", "/api/auth/login", "/api/auth/logout"} for row in rows), "diagnostics_bounded_routes")
        check(any(row.get("origin_state") == "NULL" and row.get("csrf_state") == "VALID" for row in rows), "diagnostics_categorical")
        check(any(row.get("fetch_site_state") == "CROSS_SITE" and row.get("rejection_reason") == "CROSS_SITE" for row in rows), "diagnostics_fetch_site")
        evidence.append("sanitized diagnostics ≤100 rows, private mode 0600; no password/token/query values")

        print("HTTP_BIND=127.0.0.1;TEST_ONLY=YES;PROVIDER_CALLS=0;REAL_DATA=NO")
        for item in evidence:
            print("PASS " + item)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        store.close()


def server_child(root: Path) -> int:
    adm, store, server, origin = setup_server(root)
    print(json.dumps({"event": "READY", "host": "127.0.0.1", "port": server.server_address[1]}), flush=True)
    try:
        server.serve_forever()
        return 0
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_environment(root: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(root / "tmp"),
        "BMT_ADMIN_PRIVATE_ROOT": str(root / "admin-private"),
        "BMT_ADMIN_PREVIEW_ROOT": str(root / "preview"),
        "BMT_ADMIN_STAGING_ROOT": str(root / "staging"),
        "BMT_ADMIN_PUBLIC_ASSETS_ROOT": str(root / "public-assets"),
        "BMT_ADMIN_HOST": "127.0.0.1", "BMT_ADMIN_PORT": "0",
        "BMT_ADMIN_PUBLIC_ORIGIN": "", "BMT_ADMIN_ALLOWED_IP": "",
        "BMT_ADMIN_OWNER_ID": "TEST_ONLY_OWNER", "BMT_ADMIN_PASSWORD_FILE": str(root / "owner-password"),
        "BMT_ADMIN_LOCAL_HTTP_DEV": "1", "BMT_ADMIN_SECURE_COOKIE": "0",
        "BMT_ENV": "test", "BMT_OPENAI_EXTRACTION": "0", "BMT_OPENAI_CONTENT_LIVE": "0",
    }


def browser_server() -> int:
    with tempfile.TemporaryDirectory(prefix="sala-issue39-browser-login-") as temp:
        root = Path(temp); root.chmod(0o700); (root / "tmp").mkdir(mode=0o700)
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--server-child", str(root)],
                                   env=test_environment(root), stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            line = process.stdout.readline() if process.stdout else ""
            ready = json.loads(line)
            if ready.get("event") != "READY" or ready.get("host") != "127.0.0.1":
                return 2
            print(f"BROWSER_TEST_URL=http://127.0.0.1:{ready['port']}/login", flush=True)
            process.wait()
            return 0
        except KeyboardInterrupt:
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        return child(Path(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--server-child":
        return server_child(Path(sys.argv[2]))
    if len(sys.argv) == 2 and sys.argv[1] == "--serve-browser":
        return browser_server()
    with tempfile.TemporaryDirectory(prefix="sala-issue39-login-csrf-") as temp:
        root = Path(temp); root.chmod(0o700)
        (root / "tmp").mkdir(mode=0o700)
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(root)],
                                   env=test_environment(root), capture_output=True, text=True, timeout=45)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode:
            print("LOGIN_CSRF_TEST_FAILED=" + str(completed.returncode))
            return completed.returncode
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
