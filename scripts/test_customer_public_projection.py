"""TEST_ONLY Admin projection → Customer Web public read path. All files/databases are temporary."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from customer_web_v2.sala_web import Application
from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.projection_builder import ProjectionError, build_public_projection
from property_memory.review_center import ReviewCenter
from property_memory.staging_artifact_writer import write_staging_web_artifact
import property_memory.projection_builder as projection_builder
import admin.admin_server as admin_server

ORIGIN = "https://synthetic.example"
PASSWORD = "TEST_ONLY-owner-password-314!"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")


class Reliability:
    def audit(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        return {"status": "queued"}


def fixture_intake() -> dict:
    return {
        "id": "intake-test-only-public-projection-01", "source": "Cá nhân",
        "sourceGroup": "TEST_ONLY", "note": "Synthetic test only",
        "rightsStatus": "PERSONAL", "missingFields": [], "conflicts": [],
        "preliminaryFacts": {
            "title": "TEST_ONLY Property", "property_type": "HOUSE", "transaction_type": "SALE",
            "address": "TEST_ONLY Address", "area": "72.5", "width": 5, "length": 14.5,
            "land_area": 72.5, "price_vnd": 2500000000, "legal_status": "CERTIFICATE",
            "road_access": "TEST_ONLY", "description": "Synthetic approved facts only",
            "house_type": "TEST_ONLY house", "bedrooms": 2, "bathrooms": 1,
        },
        "media": [{"filename": "front.png", "path": "assets/images/test-only-property/front.png", "sha256": "synthetic-sha256"}],
    }


class CustomerProjectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sala-firstweb-projection-")
        self.root = Path(self.temp.name)
        self.preview_root = self.root / "admin-preview"
        self.preview_root.mkdir(mode=0o700)
        self.previous = {key: os.environ.get(key) for key in (
            "BMT_ADMIN_PRIVATE_ROOT", "BMT_ADMIN_PREVIEW_ROOT", "BMT_ADMIN_HOST", "BMT_ADMIN_PORT",
            "BMT_ADMIN_LOCAL_HTTP_DEV", "BMT_ENV", "BMT_ADMIN_ALLOWED_IP", "BMT_ADMIN_PUBLIC_ORIGIN",
        )}
        os.environ.update({
            "BMT_ADMIN_PRIVATE_ROOT": str(self.root / "admin-private"),
            "BMT_ADMIN_PREVIEW_ROOT": str(self.preview_root),
            "BMT_ADMIN_HOST": "127.0.0.1", "BMT_ADMIN_PORT": "43991",
            "BMT_ADMIN_LOCAL_HTTP_DEV": "1", "BMT_ENV": "test",
        })
        os.environ.pop("BMT_ADMIN_ALLOWED_IP", None)
        os.environ.pop("BMT_ADMIN_PUBLIC_ORIGIN", None)

        self.store = PropertyStore(self.root / "admin-core.sqlite3")
        self.center = ReviewCenter(self.store, reliability=ReliabilityBoundary(Reliability().audit, Reliability().enqueue))
        package = process_intake(self.store, self.center, fixture_intake())
        self.review_id = package["review_id"]
        approved = self.center.approve(
            self.review_id, expected_revision=1, owner_id="TEST_ONLY-owner",
            decision="CREATE_NEW_PROPERTY", idempotency_key="TEST_ONLY-gate-a-r1",
        )
        self.property_id = approved["mutation_result"]["property_id"]
        appearance = self.store.list_source_appearances(self.property_id)[0]
        self.store.add_rights(self.property_id, {
            "appearance_id": appearance["appearance_id"], "rights_status": "PERSONAL",
            "channel": "public_web", "approved": True,
        })

        self.assets_root = self.root
        self.previous_project_root = projection_builder.PROJECT_ROOT
        asset = self.assets_root / "assets/images/test-only-property/front.png"
        asset.parent.mkdir(parents=True, mode=0o700)
        asset.write_bytes(PNG)
        projection_builder.PROJECT_ROOT = self.assets_root
        self.projection_path, self.web_path = admin_server._public_preview_paths()
        self.app = Application(
            self.root / "customer-private/customer.sqlite3", PASSWORD, {ORIGIN},
            public_projection_path=self.web_path, public_assets_root=self.assets_root,
        )

    def tearDown(self):
        self.store.close()
        projection_builder.PROJECT_ROOT = self.previous_project_root
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def _approved_dto(self) -> dict:
        return json.loads(self.web_path.read_text(encoding="utf-8"))[0]

    def _publish_fixture(self) -> dict:
        self.center.record_public_release(
            self.review_id, expected_revision=1, property_id=self.property_id,
            owner_id="TEST_ONLY-owner", action="APPROVE_PUBLICATION",
            idempotency_key="TEST_ONLY-web-publish-r1",
        )
        projected = build_public_projection(self.store, self.property_id, self.projection_path)["projection"]
        write_staging_web_artifact(projected, self.web_path)
        return projected

    def test_gate_a_and_web_publication_and_customer_feed_detail_media(self):
        # (a) Nothing before Gate A/public release; this Store only contains a synthetic pending fixture.
        self.assertFalse(self.web_path.exists())
        self.assertEqual(self.app.handle("GET", "/api/listings").status, 503)
        pending = self.store.create({"title": "TEST_ONLY pending property", "property_revision": 1, "lifecycle_state": "NEEDS_REVIEW"}, property_id="property-test-only-pending")
        with self.assertRaisesRegex(ProjectionError, "APPROVAL_EVIDENCE_REQUIRED"):
            build_public_projection(self.store, pending["property_id"], self.projection_path)

        # (b) Gate A alone cannot create a Customer-visible projection.
        with self.assertRaisesRegex(ProjectionError, "PUBLIC_RELEASE_APPROVAL_REQUIRED"):
            build_public_projection(self.store, self.property_id, self.projection_path)
        self.assertFalse(self.web_path.exists())
        self.assertEqual(self.app.handle("GET", "/api/listings").status, 503)

        # (c) Explicit Owner website-publication decision creates the existing allowlisted artifact.
        dto = self._publish_fixture()
        response = self.app.handle("GET", "/api/listings?q=TEST_ONLY")
        self.assertEqual(response.status, 200)
        rows = json.loads(response.body)["items"]
        self.assertEqual(len(rows), 1)
        listing = rows[0]
        self.assertEqual(listing["property_id"], dto["propertyId"])
        self.assertEqual(listing["property_revision"], dto["propertyRevision"])
        self.assertEqual(listing["projection_id"], dto["projectionId"])
        self.assertEqual(listing["publication_revision"], dto["publicationRevision"])
        self.assertEqual(self.app.handle("GET", "/api/listings?transaction_type=RENT").body, b'{"items":[]}')
        detail = self.app.handle("GET", "/api/listings/" + listing["listing_id"])
        self.assertEqual(detail.status, 200)
        detail_obj = json.loads(detail.body)
        self.assertEqual((detail_obj["property_id"], detail_obj["property_revision"]), (dto["propertyId"], dto["propertyRevision"]))
        self.assertNotIn("owner_contact", detail_obj)
        media_id = detail_obj["media"][0]["media_id"]
        media = self.app.handle("GET", "/media/" + media_id)
        self.assertEqual((media.status, media.body), (200, PNG))

    def test_stale_revision_cleanup_revoke_and_no_duplicate_on_replay_or_update(self):
        self._publish_fixture()
        first = self._approved_dto()
        self.assertEqual(len(json.loads(self.web_path.read_text(encoding="utf-8"))), 1)
        write_staging_web_artifact(first, self.web_path)
        self.assertEqual(len(json.loads(self.web_path.read_text(encoding="utf-8"))), 1)  # idempotent replay

        # (d) A canonical revision advance makes the prior approval stale; Admin's existing cleanup removes it.
        self.store.update(self.property_id, {"property_revision": 2, "price": 2600000000})
        stale = admin_server._public_release_snapshot(self.center, self.review_id, cleanup_stale_preview=True)
        self.assertEqual(stale["state"], "STALE")
        self.assertEqual(json.loads(self.web_path.read_text(encoding="utf-8")), [])
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"], [])

        # A new matching publication revision replaces, rather than duplicates, the old public slot.
        self.center.record_public_release(
            self.review_id, expected_revision=1, property_id=self.property_id,
            owner_id="TEST_ONLY-owner", action="APPROVE_PUBLICATION",
            idempotency_key="TEST_ONLY-web-publish-r2",
        )
        newer = build_public_projection(self.store, self.property_id, self.projection_path)["projection"]
        write_staging_web_artifact(newer, self.web_path)
        self.assertEqual(newer["propertyRevision"], 2)
        self.assertEqual(len(json.loads(self.web_path.read_text(encoding="utf-8"))), 1)
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"][0]["property_revision"], 2)

        # (e) Revoke withdraws the projection and media lookup fails closed.
        self.center.record_public_release(
            self.review_id, expected_revision=1, property_id=self.property_id,
            owner_id="TEST_ONLY-owner", action="REVOKE_PUBLICATION",
            idempotency_key="TEST_ONLY-web-revoke-r2", reason="TEST_ONLY revoke",
        )
        admin_server._withdraw_private_preview(self.property_id)
        self.assertEqual(json.loads(self.web_path.read_text(encoding="utf-8")), [])
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"], [])
        self.assertEqual(self.app.handle("GET", "/media/" + PublicMediaId(newer, newer["images"][0])).status, 404)

    def test_invalid_rights_private_fields_duplicates_and_missing_source_fail_closed(self):
        dto = self._publish_fixture()
        rows = [copy.deepcopy(dto)]

        # (f) Unapproved media-rights and private data are never served.
        rows[0]["mediaRights"][rows[0]["images"][0]] = "UNKNOWN"
        self.web_path.write_text(json.dumps(rows), encoding="utf-8")
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"], [])
        rows[0] = copy.deepcopy(dto)
        rows[0]["owner_contact"] = "TEST_ONLY-private"
        self.web_path.write_text(json.dumps(rows), encoding="utf-8")
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"], [])

        # (g) Duplicate/replayed identities are blocked rather than multiplied.
        self.web_path.write_text(json.dumps([dto, dto]), encoding="utf-8")
        self.assertEqual(json.loads(self.app.handle("GET", "/api/listings").body)["items"], [])

        # (h) Unavailable or malformed projection returns an error and never falls back to approved B SQLite data.
        no_source = Application(self.root / "customer-private/no-source.sqlite3", PASSWORD, {ORIGIN})
        cookie = self._login(no_source)
        self._legacy_approved_listing(no_source, cookie)
        self.assertEqual(no_source.handle("GET", "/api/listings").status, 503)
        malformed = self.root / "bad-projection.json"
        malformed.write_text("not-json", encoding="utf-8")
        bad_source = Application(
            self.root / "customer-private/bad-source.sqlite3", PASSWORD, {ORIGIN},
            public_projection_path=malformed, public_assets_root=self.assets_root,
        )
        self.assertEqual(bad_source.handle("GET", "/api/listings").status, 503)

    def _login(self, app: Application) -> str:
        response = app.handle("POST", "/api/login", {"Origin": ORIGIN}, json.dumps({"password": PASSWORD}).encode())
        self.assertEqual(response.status, 200)
        from http.cookies import SimpleCookie
        jar = SimpleCookie(); jar.load(response.headers["Set-Cookie"])
        return jar["sala_owner_session"].value

    def _legacy_approved_listing(self, app: Application, cookie: str) -> None:
        from customer_web_v2.tests.test_customer_web import listing_fields, multipart
        content_type, body = multipart(listing_fields(title="TEST_ONLY legacy row"))
        response = app.handle("POST", "/api/intakes", {"Origin": ORIGIN, "Cookie": f"sala_owner_session={cookie}", "Content-Type": content_type}, body)
        self.assertEqual(response.status, 201)
        listing_id = json.loads(response.body)["listing_id"]
        reviewed = app.handle("POST", f"/api/listings/{listing_id}/review", {"Origin": ORIGIN, "Cookie": f"sala_owner_session={cookie}", "Content-Type": "application/json"}, json.dumps({"action": "approve"}).encode())
        self.assertEqual(reviewed.status, 200)
        self.assertEqual(app.handle("GET", "/api/listings").status, 503)


def PublicMediaId(dto: dict, ref: str) -> str:
    from customer_web_v2.projection_reader import PublicProjectionReader
    return PublicProjectionReader._media_id(dto, ref)


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_create_any_default_storage(self):
        with tempfile.TemporaryDirectory(prefix="sala-import-safety-") as folder:
            root = Path(folder)
            env = dict(os.environ)
            env.update({
                "BMT_ADMIN_PRIVATE_ROOT": str(root / "admin-private"),
                "BMT_ADMIN_PREVIEW_ROOT": str(root / "preview"),
                "SALA_DB_PATH": str(root / "customer.sqlite3"),
                "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "customer_web_v2"))),
            })
            result = subprocess.run([
                sys.executable, "-c",
                "import admin.admin_server; import sala_web; import property_memory.public_contract_adapter",
            ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "admin-private").exists())
            self.assertFalse((root / "customer.sqlite3").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
