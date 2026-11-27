"""TEST_ONLY loopback HTTP contract for stable Customer Web listing URLs."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="issue39-public-link-") as folder:
        root = Path(folder)
        root.chmod(0o700)
        projection = root / "projection.json"
        previous = {key: os.environ.get(key) for key in ("BMT_CUSTOMER_PUBLIC_BASE_URL", "BMT_TEST_ONLY", "BMT_ENV")}
        os.environ["BMT_TEST_ONLY"] = "1"
        os.environ["BMT_ENV"] = "test"
        from customer_web_v2.sala_web import Application
        app = Application(root / "not-created.sqlite3", allowed_origins=set(),
                          public_projection_path=projection, public_assets_root=root,
                          legacy_owner_enabled=False)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                result = app.handle("GET", self.path, dict(self.headers), client_ip="127.0.0.1")
                self.send_response(result.status)
                for key, value in result.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(result.body)
            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        os.environ["BMT_CUSTOMER_PUBLIC_BASE_URL"] = base
        try:
            import admin.admin_server as admin
            property_id = "TEST_ONLY-property-4b1a"
            listing_id = "listing-" + __import__("hashlib").sha256(f"{property_id}:SALE".encode()).hexdigest()[:24]
            dto = {
                "id": "sample-house", "listingId": listing_id, "slug": "sample-house",
                "title": "TEST_ONLY approved sample", "status": "Đang bán", "price": 1234567890,
                "features": [], "images": [], "updatedAt": "TEST_ONLY", "propertyType": "HOUSE",
                "addressText": "TEST_ONLY location", "area": 72, "transactionType": "SALE",
                "description": "TEST_ONLY public text", "propertyId": property_id, "propertyRevision": 1,
                "projectionId": "projection-TEST_ONLY-r1", "publicationId": "publication-" + "a" * 32,
                "projectionGeneratedAt": "TEST_ONLY", "sourceSystem": "SALA_PROPERTY_CORE",
                "publicationStatus": "APPROVED", "publicationRevision": 1,
                "publicationApprovedAt": "TEST_ONLY", "mediaRights": {},
            }
            projection.write_text(json.dumps([dto], ensure_ascii=False), encoding="utf-8")
            first = admin._verify_customer_public_listing(dto)
            assert first["verified"] is True, first
            assert first["public_url"] == base + "/tin/" + listing_id
            with urlopen(base + "/api/listings/" + listing_id, timeout=3) as response:
                detail = json.loads(response.read())
                assert response.status == 200
            assert detail["listing_id"] == listing_id and detail["property_revision"] == 1
            assert "owner_contact" not in detail and "candidate" not in detail
            with urlopen(first["public_url"], timeout=3) as response:
                assert response.status == 200 and response.headers.get("Content-Type", "").startswith("text/html")
                assert b"/app.js" in response.read()

            # Same public identity on a newly approved projection revision.
            dto.update({"propertyRevision": 2, "publicationRevision": 2,
                        "projectionId": "projection-TEST_ONLY-r2", "publicationId": "publication-" + "b" * 32})
            projection.write_text(json.dumps([dto], ensure_ascii=False), encoding="utf-8")
            second = admin._verify_customer_public_listing(dto)
            assert second["verified"] is True and second["public_url"] == first["public_url"]
            with urlopen(second["public_url"], timeout=3):
                pass
            with urlopen(base + "/api/listings/" + listing_id, timeout=3) as response:
                assert json.loads(response.read())["property_revision"] == 2

            # Stale identity and an unpaired remote HTTP origin fail closed.
            mismatch = admin._verify_customer_public_listing({**dto, "propertyId": "TEST_ONLY-other-property"})
            assert mismatch["verified"] is False and mismatch["reason"] == "CUSTOMER_DETAIL_IDENTITY_MISMATCH"
            os.environ["BMT_CUSTOMER_PUBLIC_BASE_URL"] = "http://customer.example.invalid"
            bad_origin = admin._verify_customer_public_listing(dto)
            assert bad_origin["verified"] is False and bad_origin["reason"] == "CUSTOMER_PUBLIC_BASE_URL_INVALID"
            os.environ["BMT_CUSTOMER_PUBLIC_BASE_URL"] = base

            # Revoke / unknown IDs: no direct HTML and no API data.
            projection.write_text("[]", encoding="utf-8")
            assert admin._verify_customer_public_listing(dto)["verified"] is False
            try:
                urlopen(first["public_url"], timeout=3)
                raise AssertionError("revoked link unexpectedly served")
            except Exception as exc:
                from urllib.error import HTTPError
                assert isinstance(exc, HTTPError) and exc.code == 404, type(exc)
            invalid = admin._verify_customer_public_listing({**dto, "listingId": "not-a-listing-id"})
            assert invalid["verified"] is False and invalid["reason"] == "PUBLIC_LISTING_ID_INVALID"
            assert not (root / "not-created.sqlite3").exists()
            print("ISSUE39_STABLE_LISTING_URL_TEST_ONLY_PASS: loopback Customer HTTP detail=200; direct URL=200; same canonical link after r1→r2; current detail revision=2; revoke/unknown=404; Admin URL verifier exact identity PASS; Customer SQLite absent")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    main()
