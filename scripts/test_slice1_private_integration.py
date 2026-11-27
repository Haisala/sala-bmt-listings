"""Private Slice 1 integration test using temporary SQLite/private storage."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from property_memory import PropertyStore
from property_memory.ai_gateway import AIGateway
from property_memory.intake_review import process_intake
from property_memory.review_center import PENDING_REVIEW, ReviewCenter


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bmt-slice1-") as folder:
        db_path = Path(folder) / "property-memory.sqlite3"
        store = PropertyStore(db_path)
        before_public = (ROOT / "data/listings.json").read_bytes()
        intake = {
            "id": "intake-slice1-private",
            "source": "Cá nhân",
            "sourceGroup": "Hải",
            "sourceDate": "2026-11-04T00:00:00Z",
            "note": "Nhà 5x27, giá chào 1 tỷ 850",
            "preliminaryFacts": {"width": 5, "length": 27, "land_area": 135, "price_vnd": 1850000000},
            "media": [
                {"filename": "front.jpg", "path": "inbox/intake-slice1-private/front.jpg", "sha256": "sha-front", "bytes": 10},
                {"filename": "living.jpg", "path": "inbox/intake-slice1-private/living.jpg", "sha256": "sha-living", "bytes": 11},
            ],
            "missingFields": ["legal_status", "address"],
            "rightsStatus": "UNKNOWN",
        }
        center = ReviewCenter(store)

        class NeverCallProvider:
            calls = 0

            def extract_validated(self, _source):
                self.calls += 1
                raise AssertionError("disabled extraction provider must not run")

        provider = NeverCallProvider()
        gateway = AIGateway(provider=provider, enabled=False, allowed_models={"TEST_ONLY"}, model="TEST_ONLY")
        first = process_intake(store, center, intake, ai_gateway=gateway, operation_id=intake["id"])
        replay = process_intake(store, center, intake, ai_gateway=gateway, operation_id=intake["id"])
        assert first["state"] == PENDING_REVIEW
        assert first["review_id"] == replay["review_id"]
        assert first["candidate"]["candidate_id"] == replay["candidate"]["candidate_id"]
        assert first["source_bundle_id"].startswith("bundle-")
        assert [x["sha256"] for x in first["candidate"]["media"]] == ["sha-front", "sha-living"]
        assert first["match_results"][0]["classification"] == "NEW_PROPERTY"
        assert store.list_properties() == []
        assert store.schema_version() == 7
        assert replay["review_revision"] == first["review_revision"] == 1
        assert len(center.packages) == 1 and provider.calls == 0
        edited = center.edit(first["review_id"], expected_revision=1, field="description",
                             value="TEST_ONLY owner edit must be retained", owner_id="TEST_ONLY_OWNER")
        replay_after_edit = process_intake(store, center, intake, ai_gateway=gateway, operation_id=intake["id"])
        assert replay_after_edit["state"] == edited["state"] == "EDITED"
        assert replay_after_edit["review_revision"] == edited["review_revision"] == 2
        assert replay_after_edit["owner_edits"] == edited["owner_edits"]
        assert replay_after_edit["candidate"]["facts"]["description"] == "TEST_ONLY owner edit must be retained"
        assert len(center.packages) == 1 and provider.calls == 0
        store.close()

        reopened = PropertyStore(db_path)
        restored = ReviewCenter(reopened).view(first["review_id"])
        assert restored["state"] == "EDITED" and restored["review_revision"] == 2
        assert restored["candidate"]["intake_id"] == intake["id"]
        assert restored["candidate"]["media"][0]["sha256"] == "sha-front"
        assert len(reopened.connection.execute("SELECT * FROM review_packages").fetchall()) == 1
        assert (ROOT / "data/listings.json").read_bytes() == before_public
        print("PRIVATE_TEST_PASS: intake, candidate/matching, one persistent review, replay preserves Owner edit/revision, disabled provider never called, no public mutation")


if __name__ == "__main__":
    main()
