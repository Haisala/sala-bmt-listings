import json
import sys
import tempfile
import unittest
from unittest.mock import Mock
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from sala_web import Application, AuthError, parse_multipart


ORIGIN = "https://haisala.io.vn"
PASSWORD = "synthetic-test-owner-password-35!"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")


def multipart(fields, files=()):
    boundary = "SALA-TEST-BOUNDARY"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    for name, filename, mime, payload in files:
        chunks.extend([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode(),
            payload, b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def listing_fields(**override):
    fields = {
        "transaction_type": "SALE", "property_type": "Nhà phố TEST_ONLY",
        "title": "Nhà mẫu TEST_ONLY A", "description": "Dữ liệu giả để kiểm thử.",
        "price_vnd": "2500000000", "area_m2": "72.5", "location": "Khu giả TEST_ONLY",
        "source": "fixture synthetic",
    }
    fields.update(override)
    return fields


class CustomerWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "private" / "test.sqlite3"
        self.app = Application(self.db_path, PASSWORD, {ORIGIN})

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, obj, cookie=None, origin=ORIGIN):
        headers = {"Content-Type": "application/json", "Origin": origin}
        if cookie:
            headers["Cookie"] = f"sala_owner_session={cookie}"
        return self.app.handle("POST", path, headers, json.dumps(obj).encode())

    def login(self, password=PASSWORD):
        r = self.post("/api/login", {"password": password})
        self.assertEqual(r.status, 200)
        jar = SimpleCookie(); jar.load(r.headers["Set-Cookie"])
        return jar["sala_owner_session"].value

    def intake(self, fields=None, files=(), cookie=None):
        content_type, body = multipart(fields or listing_fields(), files)
        headers = {"Content-Type": content_type, "Origin": ORIGIN}
        if cookie:
            headers["Cookie"] = f"sala_owner_session={cookie}"
        return self.app.handle("POST", "/api/intakes", headers, body)

    def test_public_build_has_no_customer_owner_or_property_store(self):
        path = Path(self.temp.name) / "public-only" / "must-not-exist.sqlite3"
        app = Application(path, allowed_origins={ORIGIN}, legacy_owner_enabled=False)
        self.assertIsNone(app.store)
        self.assertFalse(path.exists())
        self.assertEqual(json.loads(app.handle("GET", "/health").body)["storage"], "admin_projection")
        self.assertEqual(app.handle("POST", "/api/login", {"Origin": ORIGIN}, b"{}" ).status, 404)
        self.assertEqual(app.handle("GET", "/api/admin/listings").status, 404)
        self.assertEqual(app.handle("POST", "/api/intakes", {"Origin": ORIGIN}, b"" ).status, 404)
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(AuthError, "cannot configure a legacy Owner credential"):
            Application(path, PASSWORD, {ORIGIN}, legacy_owner_enabled=False)

    def test_boot_health_assets_and_secure_headers(self):
        health = self.app.handle("GET", "/health")
        self.assertEqual(health.status, 200)
        self.assertEqual(json.loads(health.body)["storage"], "sqlite")
        page = self.app.handle("GET", "/")
        self.assertEqual(page.status, 200)
        self.assertIn(b"noindex,nofollow,noarchive", page.body)
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])
        self.assertEqual(self.app.handle("GET", "/%2e%2e/app.js").status, 404)
        self.assertEqual(self.app.handle("GET", "/missing.css").status, 404)

    def test_auth_required_for_input_queue_and_review(self):
        self.assertEqual(self.app.handle("GET", "/api/admin/pending").status, 401)
        self.assertEqual(self.intake().status, 401)
        self.assertEqual(self.post("/api/login", {"password": "wrong password long"}).status, 401)
        self.assertEqual(self.post("/api/login", {"password": PASSWORD}, origin="https://attacker.example").status, 403)
        cookie = self.login()
        self.assertEqual(self.app.handle("GET", "/api/admin/pending", {"Cookie": f"sala_owner_session={cookie}"}).status, 200)

    def test_weak_bootstrap_password_is_refused(self):
        with self.assertRaises(AuthError):
            Application(Path(self.temp.name) / "weak.sqlite3", "short")

    def test_existing_private_password_file_accepts_legacy_length_and_refreshes_on_restart(self):
        source_dir = Path(self.temp.name) / "legacy-auth"
        source_dir.mkdir(mode=0o700)
        source = source_dir / "admin-password"
        source.write_text("TEST_ONLY-old", encoding="utf-8")
        source.chmod(0o600)
        db_path = Path(self.temp.name) / "legacy.sqlite3"
        app = Application(db_path, allowed_origins={ORIGIN}, owner_password_file=source)
        self.assertEqual(app.handle("POST", "/api/login", {"Origin": ORIGIN}, json.dumps({"password": "TEST_ONLY-old"}).encode()).status, 200)
        self.assertEqual(app.handle("POST", "/api/login", {"Origin": ORIGIN}, json.dumps({"password": PASSWORD}).encode()).status, 401)

        source.write_text("TEST_ONLY-new", encoding="utf-8")
        source.chmod(0o600)
        restarted = Application(db_path, allowed_origins={ORIGIN}, owner_password_file=source)
        self.assertEqual(restarted.handle("POST", "/api/login", {"Origin": ORIGIN}, json.dumps({"password": "TEST_ONLY-old"}).encode()).status, 401)
        self.assertEqual(restarted.handle("POST", "/api/login", {"Origin": ORIGIN}, json.dumps({"password": "TEST_ONLY-new"}).encode()).status, 200)

    def test_existing_password_file_must_be_private_and_regular(self):
        source_dir = Path(self.temp.name) / "legacy-auth-open"
        source_dir.mkdir(mode=0o700)
        source = source_dir / "admin-password"
        source.write_text("TEST_ONLY-password", encoding="utf-8")
        source.chmod(0o644)
        with self.assertRaises(AuthError):
            Application(Path(self.temp.name) / "insecure.sqlite3", owner_password_file=source)

        source.chmod(0o600)
        link = source_dir / "password-link"
        link.symlink_to(source)
        with self.assertRaises(AuthError):
            Application(Path(self.temp.name) / "symlink.sqlite3", owner_password_file=link)

    def test_existing_password_file_cannot_be_ambiguous_with_inline_password(self):
        source_dir = Path(self.temp.name) / "legacy-auth-conflict"
        source_dir.mkdir(mode=0o700)
        source = source_dir / "admin-password"
        source.write_text("TEST_ONLY-password", encoding="utf-8")
        source.chmod(0o600)
        with self.assertRaises(AuthError):
            Application(Path(self.temp.name) / "conflict.sqlite3", PASSWORD, {ORIGIN}, owner_password_file=source)

    def test_login_rate_limit(self):
        self.app.store.authenticate = Mock(side_effect=AuthError("invalid"))
        for _ in range(5):
            self.assertEqual(self.post("/api/login", {"password":"wrong password long"}).status, 401)
        self.assertEqual(self.post("/api/login", {"password":"wrong password long"}).status, 429)

    def test_legacy_input_review_stays_private_without_admin_projection(self):
        cookie = self.login()
        file = [("media", "room.png", "image/png", PNG)]
        created = self.intake(files=file, cookie=cookie)
        self.assertEqual(created.status, 201)
        receipt = json.loads(created.body)
        self.assertEqual(receipt["state"], "PENDING_REVIEW")
        self.assertEqual(receipt["file_count"], 1)
        self.assertEqual(self.app.handle("GET", "/api/listings").status, 503)
        pending = json.loads(self.app.handle("GET", "/api/admin/pending", {"Cookie": f"sala_owner_session={cookie}"}).body)["items"]
        self.assertEqual(len(pending), 1)
        media_id = pending[0]["media"][0]["media_id"]
        self.assertEqual(self.app.handle("GET", f"/media/{media_id}").status, 503)
        reviewed = self.post(f"/api/listings/{receipt['listing_id']}/review", {"action": "approve"}, cookie)
        self.assertEqual(json.loads(reviewed.body)["state"], "APPROVED")
        self.assertEqual(self.app.handle("GET", "/api/listings?q=TEST_ONLY").status, 503)
        self.assertEqual(self.app.handle("GET", f"/api/listings/{receipt['listing_id']}").status, 503)
        self.assertEqual(self.app.handle("GET", f"/media/{media_id}").status, 503)

    def test_reject_duplicate_and_sale_rent_identity(self):
        cookie = self.login()
        created = json.loads(self.intake(cookie=cookie).body)
        rejected = self.post(f"/api/listings/{created['listing_id']}/review", {"action": "reject"}, cookie)
        self.assertEqual(json.loads(rejected.body)["state"], "REJECTED")
        self.assertEqual(self.app.handle("GET", "/api/listings").status, 503)
        # A resubmission of the same property and transaction cannot duplicate it.
        duplicate = self.intake(cookie=cookie)
        self.assertEqual(duplicate.status, 409)
        # The same property can independently be listed for rent.
        rent = self.intake(listing_fields(transaction_type="RENT", title="Thuê nhà mẫu TEST_ONLY A", price_vnd="12000000"), cookie=cookie)
        self.assertEqual(rent.status, 201)
        self.assertEqual(json.loads(rent.body)["property_id"], created["property_id"])
        self.assertNotEqual(json.loads(rent.body)["listing_id"], created["listing_id"])

    def test_restart_persistence_and_session(self):
        cookie = self.login()
        created = json.loads(self.intake(cookie=cookie).body)
        self.app = Application(self.db_path, PASSWORD, {ORIGIN})
        queue = self.app.handle("GET", "/api/admin/pending", {"Cookie": f"sala_owner_session={cookie}"})
        self.assertEqual(queue.status, 200)
        self.assertEqual(json.loads(queue.body)["items"][0]["listing_id"], created["listing_id"])
        db_mode = self.db_path.stat().st_mode & 0o777
        self.assertEqual(db_mode, 0o600)

    def test_edit_creates_revision_and_hides_approved_listing_until_reapproved(self):
        cookie = self.login()
        created = json.loads(self.intake(files=[("media", "room.png", "image/png", PNG)], cookie=cookie).body)
        self.post(f"/api/listings/{created['listing_id']}/review", {"action":"approve"}, cookie)
        fields = listing_fields(price_vnd="2750000000", description="Revision giả TEST_ONLY")
        content_type, body = multipart(fields)
        response = self.app.handle("POST", f"/api/listings/{created['listing_id']}/revisions", {"Cookie":f"sala_owner_session={cookie}","Origin":ORIGIN,"Content-Type":content_type}, body)
        self.assertEqual(response.status, 201)
        revision = json.loads(response.body)
        self.assertEqual(revision["revision_no"], 2)
        self.assertEqual(revision["state"], "PENDING_REVIEW")
        self.assertEqual(self.app.handle("GET", f"/api/listings/{created['listing_id']}").status, 503)
        self.assertEqual(self.post(f"/api/listings/{created['listing_id']}/review", {"action":"approve"}, cookie).status, 200)
        self.assertEqual(self.app.handle("GET", f"/api/listings/{created['listing_id']}").status, 503)
        admin_item = json.loads(self.app.handle("GET", "/api/admin/listings", {"Cookie":f"sala_owner_session={cookie}"}).body)["items"][0]
        self.assertEqual((admin_item["revision_no"], admin_item["status"]), (2, "APPROVED"))

    def test_validation_malformed_image_body_size_and_no_public_leak(self):
        cookie = self.login()
        self.assertEqual(self.intake(listing_fields(price_vnd="-2"), cookie=cookie).status, 400)
        self.assertEqual(self.intake(listing_fields(area_m2="NaN"), cookie=cookie).status, 400)
        self.assertEqual(self.intake(files=[("media", "x.jpg", "image/jpeg", b"not-a-jpeg")], cookie=cookie).status, 400)
        oversized_count = [("media", f"{i}.png", "image/png", PNG) for i in range(9)]
        self.assertEqual(self.intake(files=oversized_count, cookie=cookie).status, 400)
        self.assertEqual(self.app.handle("POST", "/api/intakes", {"Origin": ORIGIN, "Content-Type": "application/json"}, b"x" * (10*1024*1024+1)).status, 413)
        self.assertEqual(self.app.handle("GET", "/api/listings").status, 503)
        with self.assertRaises(ValueError):
            parse_multipart("multipart/form-data; boundary=x", b"invalid")

    def test_xss_text_rendering_and_static_route_allowlist(self):
        js = (Path(__file__).parents[1] / "frontend" / "app.js").read_text()
        html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
        self.assertNotIn("innerHTML", js)
        self.assertIn("textContent", js)
        self.assertNotIn("tel:", html + js)
        self.assertIn("Chưa kết nối gửi liên hệ; không có dữ liệu nào được gửi.", js)
        cookie = self.login()
        xss = self.intake(listing_fields(title="<script>alert(1)</script>"), cookie=cookie)
        item = json.loads(xss.body)
        self.post(f"/api/listings/{item['listing_id']}/review", {"action":"approve"}, cookie)
        public = self.app.handle("GET", f"/api/listings/{item['listing_id']}")
        self.assertEqual(public.status, 503)  # legacy SQLite rows never become public without Admin's projection.
        self.assertEqual(self.app.handle("GET", "/../../etc/passwd").status, 404)


if __name__ == "__main__":
    unittest.main()
