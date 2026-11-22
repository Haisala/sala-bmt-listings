"""Private Slice 3 projection tests; all state lives in TemporaryDirectory."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from property_memory import PropertyStore
from property_memory.intake_review import process_intake
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.projection_builder import ProjectionError, build_public_projection, rollback_projection
from property_memory.review_center import ReviewCenter


class M2:
    def audit(self, *args, **kwargs): pass
    def enqueue(self, *args, **kwargs): return {"status": "queued"}


def intake(identifier: str, rights: str = "UNKNOWN") -> dict:
    return {"id": identifier, "source": "Cá nhân", "sourceGroup": "Hải", "note": "Nhà 5x27",
            "preliminaryFacts": {"title": "Nhà Projection", "property_type": "HOUSE", "transaction_type": "SALE", "house_type": "TEST_ONLY nhà phố", "bedrooms": 3, "bathrooms": 2, "address": "Cư Êbur",
                                 "area": "Cư Êbur", "width": 5, "length": 27, "land_area": 135,
                                 "price_vnd": 1850000000, "legal_status": "CERTIFICATE", "road_access": "8m"},
            "missingFields": [], "conflicts": [], "rightsStatus": rights,
            "media": [{"filename": "front.jpg", "path": f"inbox/{identifier}/front.jpg", "sha256": "sha-front"}]}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bmt-slice3-") as folder:
        root = Path(folder)
        store = PropertyStore(root / "property-memory.sqlite3")
        center = ReviewCenter(store, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
        package = process_intake(store, center, intake("projection-1"))
        approved = center.approve(package["review_id"], expected_revision=1, owner_id="hai",
                                  decision="CREATE_NEW_PROPERTY", idempotency_key="projection-approve")
        property_id = approved["mutation_result"]["property_id"]
        output = root / "public" / "listings.json"

        # Gate A approval alone is not public-release approval.
        try:
            build_public_projection(store, property_id, output)
            raise AssertionError("Gate A alone allowed public projection")
        except ProjectionError as exc:
            assert str(exc) == "PUBLIC_RELEASE_APPROVAL_REQUIRED"
        assert not output.exists()
        center.record_public_release(approved["review_id"], expected_revision=1, property_id=property_id, owner_id="test-owner", action="APPROVE_PUBLICATION", idempotency_key="release-r1")

        # Approved property with UNKNOWN rights is still projectable, but media is fail-closed.
        first = build_public_projection(store, property_id, output)
        dto = first["projection"]
        assert dto["propertyId"] == property_id and dto["propertyRevision"] == 1
        assert dto["projectionId"] and dto["projectionGeneratedAt"] and dto["images"] == []
        assert "candidate" not in dto and "owner_contact" not in dto and "internal_analysis" not in dto
        assert set(dto).issubset({"id", "propertyId", "propertyRevision", "projectionId", "publicationId", "projectionGeneratedAt", "slug", "title", "category", "propertyType", "addressText", "status", "price", "priceLabel", "priceNote", "area", "dimensions", "width", "length", "landArea", "legal", "road", "features", "description", "images", "videos", "reelUrl", "createdAt", "updatedAt", "areas", "locationNotes", "featured", "goodPrice", "transactionType", "priceUnit", "sourceSystem", "publicationStatus", "publicationRevision", "publicationApprovedAt", "mediaRights"})

        # Rights do not make internal inbox paths public locators.
        appearance = store.list_source_appearances(property_id)[0]
        store.add_rights(property_id, {"appearance_id": appearance["appearance_id"], "rights_status": "PERSONAL", "channel": "public_web", "approved": True})
        second = build_public_projection(store, property_id, output)
        assert second["projection"]["images"] == []  # approved inbox path still remains private

        # Exercise safe published asset, unsafe references, unknown rights, and latest revocation.
        public_slug = "nha-xanh-co-ban-cu-ebur"
        store.update(property_id, {"attributes": {"public_slug": public_slug}})
        store.append_media_observation(property_id, {"appearance_id": appearance["appearance_id"], "media_ref": "assets/images/nha-xanh-co-ban-cu-ebur/01-san-truoc.webp", "active": True}, event_id="media-public-safe")
        for index, ref in enumerate(("../../secrets/photo.jpg", "/tmp/private/photo.jpg", "https://internal.example/photo.jpg", "assets/images/other-listing/01-san-truoc.webp", "assets/images/nha-xanh-co-ban-cu-ebur/missing.jpg")):
            store.append_media_observation(property_id, {"appearance_id": appearance["appearance_id"], "media_ref": ref, "active": True}, event_id=f"media-unsafe-{index}")
        third = build_public_projection(store, property_id, output)
        assert third["projection"]["images"] == ["assets/images/nha-xanh-co-ban-cu-ebur/01-san-truoc.webp"]

        # An explicit later denial for this appearance revokes the previous grant.
        store.add_rights(property_id, {"appearance_id": appearance["appearance_id"], "rights_status": "PERSONAL", "channel": "public_web", "approved": False})
        revoked = build_public_projection(store, property_id, output)
        assert revoked["projection"]["images"] == []

        # Explicit publication revocation blocks projection; a new Owner decision is required to resume.
        center.record_public_release(approved["review_id"], expected_revision=1, property_id=property_id, owner_id="test-owner", action="REVOKE_PUBLICATION", idempotency_key="release-revoke-r2")
        try:
            build_public_projection(store, property_id, output)
            raise AssertionError("revoked public release projected")
        except ProjectionError as exc:
            assert str(exc) == "PUBLIC_RELEASE_REVOKED"
        center.record_public_release(approved["review_id"], expected_revision=1, property_id=property_id, owner_id="test-owner", action="APPROVE_PUBLICATION", idempotency_key="release-r2-again")

        # Replay is deterministic and does not append a duplicate.
        replay = build_public_projection(store, property_id, output)
        assert replay["replaced"] is True and replay["idempotent_replay"] is False
        replay_again = build_public_projection(store, property_id, output)
        assert replay_again["idempotent_replay"] is True
        assert len(json.loads(output.read_text(encoding="utf-8"))) == 1

        # New canonical revision gets a new lineage and preserves the same record slot.
        store.update(property_id, {"property_revision": 2, "title": "Nhà Projection Rev 2", "price": 1900000000})
        try:
            build_public_projection(store, property_id, output)
            raise AssertionError("stale public release reused after canonical revision change")
        except ProjectionError as exc:
            assert str(exc) == "PUBLIC_RELEASE_APPROVAL_STALE"
        center.record_public_release(approved["review_id"], expected_revision=1, property_id=property_id, owner_id="test-owner", action="APPROVE_PUBLICATION", idempotency_key="release-r2")
        newer = build_public_projection(store, property_id, output)
        assert newer["projection"]["price"] == 1900000000
        assert newer["projection"]["propertyRevision"] == 2
        assert newer["projection"]["projectionId"] != dto["projectionId"]
        assert len(json.loads(output.read_text(encoding="utf-8"))) == 1

        # Unapproved and revision-zero properties are fail-closed.
        unapproved = store.create({"title": "Draft", "property_revision": 1, "lifecycle_state": "NEEDS_REVIEW"}, property_id="property-unapproved")
        try:
            build_public_projection(store, unapproved["property_id"], root / "unapproved.json")
            raise AssertionError("unapproved property projected")
        except ProjectionError as exc:
            assert str(exc) == "APPROVAL_EVIDENCE_REQUIRED"
        rev0 = store.create({"title": "Old", "property_revision": 0, "lifecycle_state": "NEEDS_REVIEW"}, property_id="property-rev0")
        try:
            build_public_projection(store, rev0["property_id"], root / "rev0.json")
            raise AssertionError("revision zero projected")
        except ProjectionError as exc:
            assert str(exc) == "APPROVAL_EVIDENCE_REQUIRED"

        # Failure before replace leaves the previous public artifact intact; rollback restores it.
        before = output.read_bytes()
        try:
            build_public_projection(store, property_id, output, failure=lambda stage: (_ for _ in ()).throw(RuntimeError(stage)))
            raise AssertionError("failure injection did not fire")
        except RuntimeError as exc:
            assert str(exc) == "before_replace"
        assert output.read_bytes() == before
        rollback_projection(output)
        assert output.exists() and output.read_bytes() == before
        print("PRIVATE_TEST_PASS: approved DTO, revision lineage, allowlist, private/public media locators, scoped rights/revocation, replay, atomic failure, rollback, unapproved/revision-zero block")


if __name__ == "__main__":
    main()
