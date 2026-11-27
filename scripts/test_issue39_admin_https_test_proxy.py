from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin import admin_server


class UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"path": self.path, "origin": self.headers.get("Origin")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(201 if body == b"TEST_ONLY" else 400)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


class AdminTestProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="issue39-admin-proxy-")
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.control = self.root / "control"
        self.control.mkdir(mode=0o700)
        self.token_path = self.control / "token"
        self.token_path.write_text("TEST_ONLY-admin-proxy-bearer-token-0123456789", encoding="ascii")
        self.token_path.chmod(0o600)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.port = self.upstream.server_address[1]
        self.admin = ThreadingHTTPServer(("127.0.0.1", 0), admin_server.Handler)
        self.previous = {key: os.environ.get(key) for key in ("BMT_TEST_NAMESPACE_PROXY_PORT", "BMT_TEST_NAMESPACE_TOKEN_FILE", "BMT_ENV")}
        os.environ["BMT_TEST_NAMESPACE_PROXY_PORT"] = str(self.port)
        os.environ["BMT_TEST_NAMESPACE_TOKEN_FILE"] = str(self.token_path)
        os.environ["BMT_ENV"] = "staging"
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (self.upstream, self.admin)]
        for thread in self.threads:
            thread.start()
        self.origin = f"http://127.0.0.1:{self.admin.server_address[1]}"

    def tearDown(self):
        for server in (self.admin, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def call(self, path="/api/health", *, method="GET", token=None, data=None, headers=None):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = "Bearer " + token
        req = Request(self.origin + "/__issue39_test_admin__" + path, data=data, headers=h, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def test_disabled_and_unauthorized_fail_closed(self):
        os.environ.pop("BMT_TEST_NAMESPACE_PROXY_PORT")
        self.assertEqual(self.call()[0], 404)
        os.environ["BMT_TEST_NAMESPACE_PROXY_PORT"] = str(self.port)
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call(token="wrong-token" * 5)[0], 401)

    def test_valid_bearer_forwards_only_to_loopback_child(self):
        token = self.token_path.read_text(encoding="ascii")
        status, payload = self.call("/api/health?q=TEST_ONLY", token=token,
                                    headers={"Origin": "https://admin-next.haisala.io.vn"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"path": "/api/health?q=TEST_ONLY", "origin": "https://admin-next.haisala.io.vn"})
        self.assertEqual(self.call("/api/intakes", method="POST", token=token, data=b"TEST_ONLY")[0], 201)
        self.assertEqual(self.call("/api/health", method="PUT", token=token)[0], 405)


if __name__ == "__main__":
    unittest.main(verbosity=2)
