"""Synthetic authenticated public-release Admin API/UI regression tests."""
from __future__ import annotations

import copy
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

from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.review_center import ReviewCenter


class M2:
    def audit(self, *args, **kwargs): pass
    def enqueue(self, *args, **kwargs): return {"status": "queued"}


def fake_intake(identifier: str, *, title: str = "TEST_ONLY home", price: int = 1850000000) -> dict:
    return {
        "id": identifier, "source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY fixture",
        "preliminaryFacts": {"title": title, "property_type": "HOUSE", "transaction_type": "SALE",
                             "address": "TEST_ONLY Cư Êbur " + identifier, "area": "TEST_ONLY Cư Êbur " + identifier, "width": 5,
                             "length": 27, "land_area": 135, "price_vnd": price,
                             "legal_status": "CERTIFICATE", "house_type": "TEST_ONLY townhouse",
                             "bedrooms": 3, "bathrooms": 2},
        "missingFields": [], "conflicts": [], "rightsStatus": "UNKNOWN", "media": [],
    }


def make_handler(adm, path: str, *, method: str, payload: dict | None = None,
                 owner: str | None = "owner-primary", origin: str | None = None):
    response: dict = {}
    sid = "synthetic-session-" + uuid.uuid4().hex
    if owner is not None:
        adm.SESSIONS[sid] = time.time() + 3600
        adm.SESSION_OWNERS[sid] = owner
    body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else b""
    headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if owner is not None:
        headers["Cookie"] = "bmt_admin_session=" + sid
    handler = adm.Handler.__new__(adm.Handler)
    handler.path = path
    handler.command = method
    handler.client_address = ("127.0.0.1", 40000)
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.send_json = lambda value, status=200, headers=None: response.update(status=status, value=value)
    handler.send_response = lambda status: response.update(status=status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    return handler, response


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sala-public-release-") as folder:
        root = Path(folder)
        (root / "admin-private").mkdir(mode=0o700)
        (root / "public-assets").mkdir(mode=0o700)
        os.environ["BMT_ADMIN_PRIVATE_ROOT"] = str(root / "admin-private")
        os.environ["BMT_ADMIN_PREVIEW_ROOT"] = str(root / "preview")
        os.environ["BMT_ADMIN_PUBLIC_ASSETS_ROOT"] = str(root / "public-assets")
        os.environ["BMT_ADMIN_OWNER_ID"] = "owner-primary"
        os.environ["BMT_ADMIN_HOST"] = "127.0.0.1"
        os.environ["BMT_ADMIN_PORT"] = "43127"
        os.environ["BMT_ENV"] = "test"
        os.environ["BMT_ADMIN_LOCAL_HTTP_DEV"] = "1"
        os.environ.pop("BMT_ADMIN_PUBLIC_ORIGIN", None)
        os.environ.pop("BMT_ADMIN_ALLOWED_IP", None)

        import admin.admin_server as adm
        from property_memory.staging_artifact_writer import StagingArtifactError, write_staging_web_artifact

        adm.configure_review_center(None)
        store = PropertyStore(root / "core.sqlite3")
        center = ReviewCenter(store, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
        package = process_intake(store, center, fake_intake("intake-test-only-one"))
        approved = center.approve(package["review_id"], expected_revision=1, owner_id="owner-primary",
                                  decision="CREATE_NEW_PROPERTY", idempotency_key="gate-a-test-only-one")
        review_id = approved["review_id"]
        property_id = approved["mutation_result"]["property_id"]
        package_record = center.packages[review_id]
        assert not package_record.get("selected_content")
        adm.configure_review_center(center)
        canonical_path, website_path = adm._public_preview_paths()
        origin = f"http://127.0.0.1:{adm.PORT}"

        initial = adm._public_release_snapshot(center, review_id)
        assert initial["state"] == "NOT_APPROVED" and initial["can_approve"] is False
        assert initial["property_revision"] == 1 and initial["media_summary"] == {"active": 0, "eligible": 0, "blocked": 0}
        assert "PUBLIC_MEDIA_REQUIRED" in initial["blockers"] and initial["confirmation_preview"] is None
        import base64
        preview_png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWQ0AAAAASUVORK5CYII=")
        media_ref = "assets/images/test-only-home/front.png"
        media_path = root / "public-assets" / media_ref
        media_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        media_path.write_bytes(preview_png)
        appearance_id = store.list_source_appearances(property_id)[0]["appearance_id"]
        store.append_media_observation(property_id, {"appearance_id": appearance_id, "media_ref": media_ref,
                                      "rights_status": "PERSONAL", "active": True}, event_id="TEST_ONLY-confirm-preview-media")
        store.add_rights(property_id, {"appearance_id": appearance_id, "rights_status": "PERSONAL",
                                       "channel": "public_web", "approved": True}, rights_id="TEST_ONLY-preview-rights")
        initial = adm._public_release_snapshot(center, review_id)
        assert initial["can_approve"] is True and initial["media_summary"] == {"active": 1, "eligible": 1, "blocked": 0}
        confirmation = initial["confirmation_preview"]
        assert confirmation["propertyId"] == property_id and confirmation["propertyRevision"] == 1
        assert confirmation["publicationId"] is None and confirmation["publicationStatus"] == "OWNER_CONFIRMATION_PENDING"
        assert confirmation["legal"] == "CERTIFICATE" and confirmation["price"] == 1850000000
        assert confirmation["description"] == (store.get(property_id).get("factual_description") or store.get(property_id)["title"])
        assert "internal_analysis" not in confirmation and len(initial["confirmation_media"]) == 1
        assert store.get(property_id)["property_revision"] == 1
        saved_area = store.get(property_id)["area"]
        store.update(property_id, {"area": None})
        missing_area = adm._public_release_snapshot(center, review_id)
        assert "PUBLIC_FACTS_INCOMPLETE" in missing_area["blockers"] and not missing_area["can_approve"]
        store.update(property_id, {"area": saved_area})

        pending = process_intake(store, center, fake_intake("intake-test-only-pending"))
        pending_status = adm._public_release_snapshot(center, pending["review_id"])
        assert "GATE_A_APPROVAL_REQUIRED" in pending_status["blockers"] and not pending_status["can_approve"]
        # Legal is an optional fact for this property type; missing and
        # UNKNOWN must not block a release or be fabricated as confirmed.
        store.update(property_id, {"legal_status": "UNKNOWN"})
        legal_status = adm._public_release_snapshot(center, review_id)
        assert "LEGAL_STATUS_UNCONFIRMED" not in legal_status["blockers"] and legal_status["can_approve"]
        store.update(property_id, {"legal_status": None})
        missing_legal_status = adm._public_release_snapshot(center, review_id)
        assert "PUBLIC_FACTS_INCOMPLETE" not in missing_legal_status["blockers"] and missing_legal_status["can_approve"]
        store.update(property_id, {"legal_status": "CERTIFICATE"})
        attrs = dict(store.get(property_id).get("attributes") or {})
        attrs["public_status"] = "Đang cho thuê"
        store.update(property_id, {"attributes": attrs})
        assert "PUBLIC_STATUS_UNSUPPORTED" in adm._public_release_snapshot(center, review_id)["blockers"]
        attrs.pop("public_status")
        store.update(property_id, {"attributes": attrs})
        store.create_conflict(property_id, "price", ["TEST_ONLY-event"])
        conflict_status = adm._public_release_snapshot(center, review_id)
        assert "OPEN_PROPERTY_CONFLICTS" in conflict_status["blockers"] and not conflict_status["can_approve"]
        conflict = store.list_conflicts(property_id)[0]
        store.resolve_conflict(conflict["conflict_id"], "DISMISSED", "TEST_ONLY fixture resolved")
        assert adm._public_release_snapshot(center, review_id)["can_approve"]

        # A selected Website Content revision must remain exact and needs approved public media.
        package_record["selected_content"] = {
            "content_batch_id": "batch-TEST_ONLY", "content_variant_id": "variant-TEST_ONLY",
            "content_revision": 2, "facts_revision_id": f"facts-{property_id}-r1",
            "property_id": property_id, "property_revision": 1, "status": "OWNER_APPROVED",
            "title": "TEST_ONLY approved title", "body": "TEST_ONLY approved public description",
            "channel_approval": {"channel": "public_web", "content_variant_id": "variant-TEST_ONLY",
                                 "content_revision": 2, "status": "OWNER_APPROVED_DRAFT"},
        }
        center._persist(package_record)
        with store.connection:
            store.connection.execute("DELETE FROM rights_refs WHERE rights_id = ?", ("TEST_ONLY-preview-rights",))
            store.connection.execute("DELETE FROM property_media_history WHERE event_id = ?", ("TEST_ONLY-confirm-preview-media",))
        content_blocked = adm._public_release_snapshot(center, review_id)
        assert "PUBLIC_MEDIA_REQUIRED" in content_blocked["blockers"]
        assert "PUBLIC_CONTENT_MEDIA_REQUIRED" in content_blocked["blockers"] and not content_blocked["can_approve"]
        assert content_blocked["confirmation_preview"] is None
        store.append_media_observation(property_id, {"appearance_id": appearance_id, "media_ref": media_ref,
                                      "rights_status": "PERSONAL", "active": True}, event_id="TEST_ONLY-confirm-preview-media")
        store.add_rights(property_id, {"appearance_id": appearance_id, "rights_status": "PERSONAL",
                                       "channel": "public_web", "approved": True}, rights_id="TEST_ONLY-preview-rights")
        content_ready = adm._public_release_snapshot(center, review_id)
        assert content_ready["can_approve"] and content_ready["confirmation_preview"]["description"] == "TEST_ONLY approved public description"
        assert content_ready["confirmation_preview"]["contentVariantId"] == "variant-TEST_ONLY"
        assert content_ready["confirmation_preview"]["images"] == [media_ref]
        media_id = content_ready["confirmation_media"][0]["media_id"]
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/media/{media_id}", method="GET")
        handler.do_GET()
        assert response["status"] == 200 and handler.wfile.getvalue() == preview_png
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/media/{media_id}", method="GET", owner=None)
        handler.do_GET()
        assert response["status"] == 401
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/media/{'0' * 24}", method="GET")
        handler.do_GET()
        assert response["status"] == 404
        package_record.pop("selected_content")
        center._persist(package_record)

        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload={"property_id": property_id, "expected_review_revision": 1,
                                                  "expected_property_revision": 1, "idempotency_key": "csrf-denied-001"},
                                         origin="https://attacker.invalid")
        handler.do_POST()
        assert response["status"] == 403 and response["value"]["error"] == "CSRF_ORIGIN_REJECTED"

        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload={"property_id": property_id, "expected_review_revision": 1,
                                                  "expected_property_revision": 1, "idempotency_key": "unauth-test-001"},
                                         owner=None, origin=origin)
        handler.do_POST()
        assert response["status"] == 401

        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload={"property_id": property_id, "expected_review_revision": 1,
                                                  "expected_property_revision": 1, "idempotency_key": "wrong-owner-001"},
                                         owner="other-owner", origin=origin)
        handler.do_POST()
        assert response["status"] == 403 and response["value"]["error"] == "OWNER_PUBLIC_APPROVAL_REQUIRED"

        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload={"property_id": property_id, "expected_review_revision": 1,
                                                  "expected_property_revision": 1, "idempotency_key": "spoof-owner-001",
                                                  "owner_id": "owner-primary"}, origin=origin)
        handler.do_POST()
        assert response["status"] == 403 and response["value"]["error"] == "OWNER_ID_BODY_FORBIDDEN"

        before = store.get(property_id)
        property_count = len(store.list_properties())
        approval_payload = {"property_id": property_id, "expected_review_revision": 1,
                            "expected_property_revision": 1, "idempotency_key": "owner-release-test-001"}
        changed_content_payload = {**approval_payload, "expected_content_variant_id": "stale-variant",
                                   "expected_content_revision": 1, "idempotency_key": "stale-content-001"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=changed_content_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 409 and response["value"]["error"] == "STALE_PUBLIC_CONTENT_REVISION"
        assert not package_record.get("public_release_decisions")
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=approval_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 201 and response["value"]["status"] == "PREVIEW_READY"
        assert response["value"]["published"] is False and response["value"]["decision_owner"] == "owner-primary"
        first_web = json.loads(website_path.read_text(encoding="utf-8"))
        first_core_projection = json.loads(canonical_path.read_text(encoding="utf-8"))
        publication_id = response["value"]["publication_id"]
        assert first_web[0]["publicationId"] == publication_id
        assert adm._analytics_property_is_public(property_id, publication_id)
        assert not adm._analytics_property_is_public(property_id, "publication-00000000000000000000000000000000")
        assert len(first_web) == len(first_core_projection) == 1
        assert first_web[0]["propertyId"] == first_core_projection[0]["propertyId"] == property_id
        assert first_web[0]["propertyRevision"] == 1 and first_web[0]["price"] == 1850000000
        assert "internal_analysis" not in first_web[0] and store.get(property_id) == before
        assert len(store.list_properties()) == property_count

        # Same key replays safely; another key cannot create a second active release.
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=approval_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 201 and len(json.loads(website_path.read_text(encoding="utf-8"))) == 1
        duplicate_payload = {**approval_payload, "idempotency_key": "owner-release-duplicate-001"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=duplicate_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 409 and response["value"]["error"] == "PUBLICATION_ALREADY_APPROVED", response

        # A second Gate A review cannot create a competing active release for this canonical property.
        competing = copy.deepcopy(approved)
        competing["review_id"] = "review-" + uuid.uuid4().hex[:24]
        competing["candidate"]["candidate_id"] = "candidate-" + uuid.uuid4().hex[:20]
        competing["decision"] = {**competing["decision"], "revision": competing["review_revision"]}
        center.packages[competing["review_id"]] = competing
        center._persist(competing)
        competing_status = adm._public_release_snapshot(center, competing["review_id"])
        assert "PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW" in competing_status["blockers"] and not competing_status["can_approve"]
        competing_payload = {"property_id": property_id, "expected_review_revision": 1,
                             "expected_property_revision": 1, "idempotency_key": "competing-release-001"}
        handler, response = make_handler(adm, f"/api/reviews/{competing['review_id']}/public-release/approve", method="POST",
                                         payload=competing_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 422 and "PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW" in response["value"]["blockers"]

        # Revision change removes the old preview; a stale request is rejected and a fresh explicit release updates it.
        changed_attributes = dict(store.get(property_id).get("attributes") or {})
        changed_attributes["public_status"] = "Đã cọc"
        store.update(property_id, {"property_revision": 2, "price": 1900000000,
                                   "lifecycle_state": "NEEDS_REVIEW", "attributes": changed_attributes})
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release", method="GET", origin=origin)
        handler.do_GET()
        assert response["status"] == 200 and response["value"]["state"] == "STALE"
        assert response["value"]["preview_removed"] == 2
        assert json.loads(website_path.read_text(encoding="utf-8")) == []
        stale_payload = {**approval_payload, "expected_property_revision": 1, "idempotency_key": "stale-release-001"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=stale_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 409 and response["value"]["error"] == "STALE_PUBLIC_RELEASE_REVISION"
        current_payload = {**approval_payload, "expected_property_revision": 2,
                           "idempotency_key": "owner-release-revision-two"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=current_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 201
        current_web = json.loads(website_path.read_text(encoding="utf-8"))
        assert current_web[0]["propertyRevision"] == 2 and current_web[0]["price"] == 1900000000
        assert current_web[0]["status"] == "Đã cọc"
        after_release = store.get(property_id)
        assert after_release["property_revision"] == 2 and after_release["price"] == 1900000000
        assert len(store.list_properties()) == property_count

        # Unknown media rights block any new public release and withdraw the isolated preview.
        with store.connection:
            store.connection.execute("DELETE FROM rights_refs WHERE rights_id = ?", ("TEST_ONLY-preview-rights",))
            store.connection.execute("DELETE FROM property_media_history WHERE event_id = ?", ("TEST_ONLY-confirm-preview-media",))
        media_path.unlink()
        appearance = store.list_source_appearances(property_id)[0]
        store.append_media_observation(property_id, {"appearance_id": appearance["appearance_id"],
                                      "media_ref": "inbox/TEST_ONLY/photo.jpg", "rights_status": "UNKNOWN", "active": True},
                                      event_id="media-public-release-test")
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release", method="GET", origin=origin)
        handler.do_GET()
        assert response["status"] == 200 and response["value"]["state"] == "INELIGIBLE"
        assert "PUBLIC_MEDIA_RIGHTS_DENIED" in response["value"]["blockers"]
        assert json.loads(website_path.read_text(encoding="utf-8")) == []
        blocked_payload = {**current_payload, "idempotency_key": "blocked-media-release-01"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/approve", method="POST",
                                         payload=blocked_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 422 and "PUBLIC_MEDIA_RIGHTS_DENIED" in response["value"]["blockers"]

        revoke_payload = {"property_id": property_id, "expected_review_revision": 1,
                          "expected_property_revision": 2, "idempotency_key": "owner-revoke-test-only",
                          "reason": "TEST_ONLY rights not confirmed"}
        handler, response = make_handler(adm, f"/api/reviews/{review_id}/public-release/revoke", method="POST",
                                         payload=revoke_payload, origin=origin)
        handler.do_POST()
        assert response["status"] == 200 and response["value"]["status"] == "REVOKED"
        assert not adm._analytics_property_is_public(property_id, publication_id)
        assert response["value"]["preview_removed"] == 0
        assert store.get(property_id)["property_revision"] == 2 and len(store.list_properties()) == property_count

        # Preview-writer failure records a compensating revoke and restores both temp artifacts.
        second_package = process_intake(store, center, fake_intake("intake-test-only-two", title="TEST_ONLY rollback home"))
        second_approved = center.approve(second_package["review_id"], expected_revision=1, owner_id="owner-primary",
                                         decision="CREATE_NEW_PROPERTY", idempotency_key="gate-a-test-only-two")
        second_id = second_approved["mutation_result"]["property_id"]
        second_record = center.packages[second_approved["review_id"]]
        assert not second_record.get("selected_content")
        second_appearance = store.list_source_appearances(second_id)[0]["appearance_id"]
        second_media_ref = "assets/images/test-only-rollback-home/front.png"
        second_media_path = root / "public-assets" / second_media_ref
        second_media_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        second_media_path.write_bytes(preview_png)
        store.append_media_observation(second_id, {"appearance_id": second_appearance,
                                      "media_ref": second_media_ref, "rights_status": "PERSONAL", "active": True},
                                      event_id="TEST_ONLY-rollback-media")
        store.add_rights(second_id, {"appearance_id": second_appearance, "rights_status": "PERSONAL",
                                    "channel": "public_web", "approved": True}, rights_id="TEST_ONLY-rollback-rights")
        web_before = website_path.read_bytes()
        core_projection_before = canonical_path.read_bytes()
        real_writer = adm.write_staging_web_artifact
        adm.write_staging_web_artifact = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("TEST_ONLY injected writer failure"))
        fail_payload = {"property_id": second_id, "expected_review_revision": 1,
                        "expected_property_revision": 1, "idempotency_key": "rollback-release-test-01"}
        try:
            handler, response = make_handler(adm, f"/api/reviews/{second_approved['review_id']}/public-release/approve", method="POST",
                                             payload=fail_payload, origin=origin)
            handler.do_POST()
        finally:
            adm.write_staging_web_artifact = real_writer
        assert response["status"] == 503 and response["value"]["rollback"] == "PASS"
        assert response["value"]["release_revoked"] is True
        assert website_path.read_bytes() == web_before and canonical_path.read_bytes() == core_projection_before
        assert len(store.list_properties()) == property_count + 1
        assert store.get(second_id)["property_revision"] == 1

        # Authenticated Admin page exposes the same integrated control on narrow and desktop layouts.
        handler, response = make_handler(adm, "/", method="GET", origin=origin)
        handler.do_GET()
        html_page = handler.wfile.getvalue().decode("utf-8")
        assert response["status"] == 200 and "Duyệt công bố riêng" in html_page
        assert "data-release-approve" in html_page and "data-release-revoke" in html_page
        # The current Admin shell uses separate screen routes, not long-page anchors.
        assert 'data-screen-link="intake"' in html_page and 'data-screen-link="content"' in html_page
        assert 'href="#intake-section"' not in html_page and 'href="#review-section"' not in html_page
        assert "local-only" not in html_page and "AI9 TEST ONLY" not in html_page
        assert "Chạy AI9 TEST_ONLY" not in html_page and "TẠO CONTENT" in html_page
        assert "min-height:44px;flex:1 1 180px" in html_page and 'name="viewport"' in html_page
        assert "expected_content_variant_id:selected.content_variant_id||null" in html_page
        assert "XEM VÀ XÁC NHẬN ĐĂNG" in html_page and "XÁC NHẬN ĐĂNG" in html_page
        assert "PUBLIC_MEDIA_REQUIRED" in html_page and "PUBLIC_FACTS_INCOMPLETE" in html_page
        for channel in ("facebook", "zalo_company", "tiktok", "instagram", "youtube", "x", "linkedin"):
            assert channel in html_page
        assert "Quyền sử dụng: "+"'+item.rights_status" in html_page
        saved_test_env = {key: os.environ.get(key) for key in ("BMT_ENV", "BMT_TEST_ONLY", "SALA_E2E_ROOT", "BMT_ADMIN_PUBLIC_ORIGIN", "BMT_ADMIN_ALLOWED_IP")}
        saved_security = (adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP)
        adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP = True, "127.0.0.1"
        os.environ.update({"BMT_ENV": "staging", "BMT_TEST_ONLY": "1", "BMT_ADMIN_PUBLIC_ORIGIN": "https://admin.test", "BMT_ADMIN_ALLOWED_IP": "127.0.0.1"})
        os.environ.pop("SALA_E2E_ROOT", None)
        handler, response = make_handler(adm, "/api/ai9/test-only", method="POST", origin="https://admin.test")
        handler.do_POST()
        assert response["status"] == 404 and response["value"]["error"] == "TEST_ONLY_ROUTE_DISABLED", response
        handler, response = make_handler(adm, "/api/ai9/test-only", method="POST", owner=None, origin="https://admin.test")
        handler.do_POST()
        assert response["status"] == 401
        adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP = saved_security
        for key, value in saved_test_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        assert store.get(property_id)["property_revision"] == 2

        # Owner-only media-rights promotion is staging-only, hash-checked, and idempotent.
        import base64
        prior_env = os.environ.get("BMT_ENV")
        staging_root = root / "staging-root"
        staging_root.mkdir(mode=0o700)
        os.environ["BMT_ENV"] = "staging"
        os.environ["BMT_ADMIN_STAGING_ROOT"] = str(staging_root)
        os.environ["BMT_ADMIN_PUBLIC_ASSETS_ROOT"] = str(staging_root / "public-assets")
        media_intake = fake_intake("intake-test-only-media-rights")
        media_intake["preliminaryFacts"]["road_access"] = "TEST_ONLY road"
        photo = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWQ0AAAAASUVORK5CYII=")
        media_digest = __import__("hashlib").sha256(photo).hexdigest()
        media_ref = f"inbox/{media_intake['id']}/front.png"
        media_path = root / "admin-private" / media_ref
        media_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        media_path.write_bytes(photo); media_path.chmod(0o600)
        media_intake["media"] = [{"mediaId": "media-" + media_digest[:24], "filename": "front.png", "path": media_ref, "sha256": media_digest, "bytes": len(photo), "role": "photo"}]
        media_package = process_intake(store, center, media_intake)
        media_approved = center.approve(media_package["review_id"], expected_revision=1, owner_id="owner-primary", decision="CREATE_NEW_PROPERTY", idempotency_key="gate-a-media-rights-test")
        media_review_id = media_approved["review_id"]
        media_property_id = media_approved["mutation_result"]["property_id"]
        media_appearance_id = store.list_source_appearances(media_property_id)[0]["appearance_id"]
        media_property_before = store.get(media_property_id)
        rights_payload = {"property_id": media_property_id, "expected_property_revision": 1, "appearance_id": media_appearance_id, "rights_status": "PERSONAL"}
        def rights_call(owner):
            response = {}
            handler = adm.Handler.__new__(adm.Handler)
            handler.owner_id = lambda: owner
            handler.send_json = lambda value, status=200, headers=None: response.update(value=value, status=status)
            handled = handler.review_route_post(f"/api/reviews/{media_review_id}/media-rights", json.dumps(rights_payload).encode())
            assert handled is True
            return response
        assert rights_call(None)["status"] == 403
        media_rights_result = rights_call("owner-primary")
        assert media_rights_result["status"] == 201 and media_rights_result["value"]["published"] is False
        media_replay = rights_call("owner-primary")
        assert media_replay["status"] == 201 and media_replay["value"].get("idempotent_replay") is True, media_replay
        media_rows = [x for x in store.list_media_history(media_property_id) if x.get("appearance_id") == media_appearance_id]
        assert len([x for x in media_rows if x["active"]]) == 1
        public_media = next(x for x in media_rows if x["active"])
        assert public_media["media_ref"].startswith("assets/images/")
        assert (staging_root / "public-assets" / public_media["media_ref"]).read_bytes() == photo
        assert adm._media_rights_allowed(store, media_property_id, media_appearance_id)
        assert store.get(media_property_id) == media_property_before
        if prior_env is None: os.environ.pop("BMT_ENV", None)
        else: os.environ["BMT_ENV"] = prior_env
        os.environ.pop("BMT_ADMIN_STAGING_ROOT", None)
        os.environ.pop("BMT_ADMIN_PUBLIC_ASSETS_ROOT", None)

        # TEST_ONLY intake cleanup must remove the index row, not merge it back.
        saved_env = {key: os.environ.get(key) for key in ("BMT_ENV", "BMT_ADMIN_PUBLIC_ORIGIN", "BMT_ADMIN_ALLOWED_IP")}
        saved_secure_cookie, saved_allowlist = adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP
        os.environ.update({"BMT_ENV": "staging", "BMT_ADMIN_PUBLIC_ORIGIN": "https://admin.test",
                           "BMT_ADMIN_ALLOWED_IP": "127.0.0.1"})
        adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP = True, "127.0.0.1"
        cleanup_intake = fake_intake("intake-" + uuid.uuid4().hex[:24])
        adm.ensure_private()
        adm.save_index([cleanup_intake])
        cleanup_folder = adm.INBOX_ROOT / cleanup_intake["id"]
        cleanup_folder.mkdir(mode=0o700, parents=True)
        (cleanup_folder / "metadata.json").write_text(json.dumps(cleanup_intake), encoding="utf-8")
        cleanup_package = process_intake(store, center, cleanup_intake)
        cleanup_review_id = cleanup_package["review_id"]
        handler, cleanup_response = make_handler(adm, f"/api/intakes/{cleanup_intake['id']}",
                                                 method="DELETE", origin="https://admin.test")
        handler.headers["X-Test-Fixture"] = "TEST_ONLY"
        handler.do_DELETE()
        assert cleanup_response["status"] == 200 and not adm.load_index(), cleanup_response
        assert not cleanup_folder.exists() and cleanup_review_id not in center.packages
        for key, value in saved_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        adm.SECURE_COOKIE, adm.ALLOWED_REMOTE_IP = saved_secure_cookie, saved_allowlist

        # The Owner UI must start closed, replace stale duplicate panels, and
        # bind enablement only to the server's complete publication snapshot.
        ui = adm.PUBLIC_RELEASE_UI.decode("utf-8")
        assert "data-release-approve disabled" in ui
        assert "detail.querySelectorAll('#public-release').forEach(existing=>existing.remove())" in ui
        assert "const canApprove=data.can_approve===true;approve.disabled=!canApprove" in ui
        assert "'Chưa thể đăng: '+blockedReason" in ui
        assert "approve.setAttribute('aria-disabled',String(!canApprove))" in ui
        package_record["state"] = "EDITED"
        package_record["review_revision"] = int(package_record["review_revision"]) + 1
        center._persist(package_record)
        with store.connection:
            store.connection.execute("DELETE FROM rights_refs WHERE rights_id = ?", ("TEST_ONLY-preview-rights",))
            store.connection.execute("DELETE FROM property_media_history WHERE event_id = ?", ("TEST_ONLY-confirm-preview-media",))
        edited = adm._public_release_snapshot(center, review_id)
        assert edited["gate_a_state"] == "EDITED" and edited["media_summary"]["eligible"] == 0
        assert {"GATE_A_APPROVAL_REQUIRED", "PUBLIC_MEDIA_RIGHTS_DENIED"}.issubset(set(edited["blockers"])), edited
        assert edited["can_approve"] is False and edited["confirmation_preview"] is None
        handler, edited_http = make_handler(adm, f"/api/reviews/{review_id}/public-release", method="GET")
        handler.do_GET()
        assert edited_http["status"] == 200
        assert edited_http["value"]["gate_a_state"] == "EDITED"
        assert edited_http["value"]["media_summary"]["eligible"] == 0
        assert edited_http["value"]["can_approve"] is False

        print("PUBLIC_RELEASE_ADMIN_PASS: Owner session + CSRF/origin, Gate A/review/Core revision binding, replay, rights fail-closed, private Website preview, revision refresh/revoke/rollback, unchanged canonical state, TEST_ONLY EDITED/zero-eligible HTTP guard, UI duplicate/initial-disabled/authoritative-state assertions, TEST_ONLY cleanup removes intake index and private media/review")


if __name__ == "__main__":
    main()
