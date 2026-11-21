"""Private Slice 2 Gate A/revision/recovery tests using temporary SQLite."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from property_memory import PropertyStore
from property_memory.evidence_read_model import AI_INTERNAL_ANALYSIS
from property_memory.review_center import APPROVED, EDITED, PENDING_REVIEW, ReviewCenter
from property_memory.mutation_service import ReliabilityBoundary
from property_memory.intake_review import process_intake


class M2:
    def audit(self, *args, **kwargs):
        return None

    def enqueue(self, *args, **kwargs):
        return {"status": "queued"}


def intake(identifier: str, *, missing=None, conflicts=None, rights="UNKNOWN"):
    return {"id": identifier, "source": "Cá nhân", "sourceGroup": "Hải", "note": "Nhà 5x27", "preliminaryFacts": {"title": "Nhà Slice 2", "property_type": "HOUSE", "transaction_type": "SALE", "address": f"A-{identifier}", "area": f"TDP 8 {identifier}", "width": 5, "length": 27, "land_area": 135, "price_vnd": 1850000000, "legal_status": "CERTIFICATE", "road_access": "8m", "house_type": "TEST_ONLY nhà phố", "bedrooms": 3, "bathrooms": 2}, "missingFields": list(missing or []), "conflicts": list(conflicts or []), "rightsStatus": rights, "media": [{"filename": "front.jpg", "path": f"inbox/{identifier}/front.jpg", "sha256": f"sha-{identifier}"}]}


def package(store, identifier, **kwargs):
    center = ReviewCenter(store, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
    return center, process_intake(store, center, intake(identifier, **kwargs))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bmt-slice2-") as folder:
        db_path = Path(folder) / "property-memory.sqlite3"
        store = PropertyStore(db_path)

        # A proposed common fact still marked unresolved blocks until an explicit Owner edit.
        center, missing = package(store, "intake-missing", missing=["price"])
        try:
            center.approve(missing["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="missing")
            raise AssertionError("missing fields approved")
        except ValueError as exc:
            assert str(exc) == "MISSING_FIELDS_REQUIRE_OWNER"
        edited = center.edit(missing["review_id"], expected_revision=1, field="price_vnd", value=1850000000, owner_id="hai")
        assert edited["review_revision"] == 2 and edited["state"] == EDITED
        assert "price" not in edited["candidate"]["missing_fields"]
        approved_missing = center.approve(missing["review_id"], expected_revision=2, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="missing-fixed")
        assert approved_missing["state"] == APPROVED

        # Reject never mutates canonical memory.
        reject_center, rejected = package(store, "intake-reject")
        before = len(store.list_properties())
        result = reject_center.reject(rejected["review_id"], expected_revision=1, owner_id="hai", reason="not ready")
        assert result["state"] == "REJECTED" and len(store.list_properties()) == before

        # Conflict is fail-closed and UNKNOWN rights are preserved, not promoted.
        conflict_center, conflicted = package(store, "intake-conflict", conflicts=["price conflict"])
        try:
            conflict_center.approve(conflicted["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="conflict")
            raise AssertionError("conflict approved")
        except ValueError as exc:
            assert str(exc) == "CONFLICT_REQUIRES_REVIEW"
        assert store.list_properties()[0]["rights"] == {}

        # Failure after canonical mutation leaves an APPROVING intent; restart + same key recovers exactly once.
        fail_center, failing = package(store, "intake-recover")
        original_persist = fail_center._persist
        def fail_after_mutation(value):
            if value["state"] == APPROVED:
                raise RuntimeError("INJECTED_REVIEW_PERSIST_FAILURE")
            return original_persist(value)
        fail_center._persist = fail_after_mutation
        try:
            fail_center.approve(failing["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="recover-key")
            raise AssertionError("failure injection did not fire")
        except RuntimeError as exc:
            assert str(exc) == "INJECTED_REVIEW_PERSIST_FAILURE"
        assert len(store.list_properties()) == 2
        store.close()
        reopened = PropertyStore(db_path)
        recovered_center = ReviewCenter(reopened, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
        recovered = recovered_center.approve(failing["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="recover-key")
        assert recovered["state"] == APPROVED
        assert len(reopened.list_properties()) == 2
        assert reopened.get(recovered["mutation_result"]["property_id"])["property_revision"] == 1
        replay = recovered_center.approve(failing["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="recover-key")
        assert replay["state"] == APPROVED and len(reopened.list_properties()) == 2

        # Concurrent claim: a second key cannot approve while the first approval is in progress.
        concurrent_center, concurrent = package(reopened, "intake-concurrent")
        concurrent_center._claim_approval(concurrent_center.packages[concurrent["review_id"]], owner_id="hai", idempotency_key="first-key")
        try:
            concurrent_center.approve(concurrent["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="second-key")
            raise AssertionError("concurrent approval accepted")
        except ValueError as exc:
            assert str(exc) == "CONCURRENT_APPROVAL"
        done = concurrent_center.approve(concurrent["review_id"], expected_revision=1, owner_id="hai", decision="CREATE_NEW_PROPERTY", idempotency_key="first-key")
        assert done["state"] == APPROVED and len(reopened.list_properties()) == 3

        # Unauthorized HTTP boundary is fail-closed without opening a socket.
        import admin.admin_server as admin
        handler = admin.Handler.__new__(admin.Handler)
        box = {}
        handler.remote_allowed = lambda: True
        handler.session_id = lambda: None
        handler.send_json = lambda value, status=200: box.update(value=value, status=status)
        assert handler.require_auth() is False and box["status"] == 401
        print("PRIVATE_TEST_PASS: revision, reject, exactly-once, concurrent claim, recovery, missing/conflict/rights safety, auth boundary")


def test_gate_a_type_transaction_matrix() -> None:
    """Synthetic approval matrix; each case uses a disposable PropertyStore."""
    from property_memory.projection_builder import build_public_projection

    common = {"title": "TEST_ONLY unit", "address": "TEST_ONLY area", "area": "TEST_ONLY district"}
    cases = [
        ("APARTMENT", "SALE", {"price_vnd": 100}, {}),
        ("APARTMENT", "RENT", {"price_vnd_per_month": 10}, {"transactionType": "RENTAL", "priceUnit": "VND/tháng"}),
        ("HOUSE", "SALE", {"price_vnd": 100, "house_type": "TEST_ONLY", "bedrooms": 2, "bathrooms": 1}, {}),
        ("HOUSE", "RENT", {"price_vnd_per_month": 10, "house_type": "TEST_ONLY", "bedrooms": 2, "bathrooms": 1}, {"transactionType": "RENTAL", "priceUnit": "VND/tháng"}),
        ("LAND", "SALE", {"price_vnd": 100}, {}),
        ("LAND", "RENT", {"price_vnd_per_month": 10}, {"transactionType": "RENTAL", "priceUnit": "VND/tháng"}),
    ]
    for index, (ptype, txn, extra, expected) in enumerate(cases):
        with tempfile.TemporaryDirectory(prefix="bmt-gate-matrix-") as folder:
            store = PropertyStore(Path(folder) / "test.sqlite3")
            center = ReviewCenter(store, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
            facts = {**common, "property_type": ptype, "transaction_type": txn, **extra}
            item = {"id": f"TEST_ONLY-{index}", "source": "TEST_ONLY", "preliminaryFacts": facts, "missingFields": [], "conflicts": [], "rightsStatus": "UNKNOWN"}
            package = process_intake(store, center, item)
            approved = center.approve(package["review_id"], expected_revision=1, owner_id="test", decision="CREATE_NEW_PROPERTY", idempotency_key=f"gate-{index}")
            prop_id = approved["mutation_result"]["property_id"]
            saved = store.get(prop_id)
            assert saved["attributes"]["price_type"] == txn
            if expected:
                center.record_public_release(package["review_id"], expected_revision=1, property_id=prop_id, owner_id="test-owner", action="APPROVE_PUBLICATION", idempotency_key=f"release-{index}")
                projection = build_public_projection(store, prop_id, Path(folder) / "public.json")["projection"]
                for key, value in expected.items():
                    assert projection[key] == value, (ptype, txn, key, projection.get(key))
            if ptype in {"APARTMENT", "LAND"}:
                assert saved.get("legal_status") is None and saved.get("width") is None and saved.get("length") is None
            store.close()

    def blocked(facts, markers=()):
        with tempfile.TemporaryDirectory(prefix="bmt-gate-block-") as folder:
            store = PropertyStore(Path(folder) / "test.sqlite3")
            center = ReviewCenter(store, reliability=ReliabilityBoundary(M2().audit, M2().enqueue))
            item = {"id": "TEST_ONLY-block", "source": "TEST_ONLY", "preliminaryFacts": facts, "missingFields": list(markers), "conflicts": [], "rightsStatus": "UNKNOWN"}
            package = process_intake(store, center, item)
            try:
                center.approve(package["review_id"], expected_revision=1, owner_id="test", decision="CREATE_NEW_PROPERTY", idempotency_key="blocked")
                raise AssertionError("invalid Gate A approved")
            except ValueError as exc:
                assert str(exc) in {"MISSING_FIELDS_REQUIRE_OWNER", "RENT_PERIOD_NOT_SUPPORTED_BY_PROPERTY_CORE"}
            assert store.list_properties() == []
            store.close()

    base = {**common, "property_type": "APARTMENT", "transaction_type": "SALE", "price_vnd": 100}
    blocked(base, ["address"])
    for field in ("house_type", "bedrooms", "bathrooms"):
        blocked({**common, "property_type": "HOUSE", "transaction_type": "SALE", "price_vnd": 100, **{x: 1 for x in ("bedrooms", "bathrooms") if x != field}, **({"house_type": "TEST_ONLY"} if field != "house_type" else {})})
    blocked({**common, "property_type": "APARTMENT", "transaction_type": "RENT", "price_vnd": 100})
    blocked({**common, "property_type": "APARTMENT", "transaction_type": "RENT", "price_vnd_per_month": 10, "price_cycle": "SEASON"})
    print("GATE_A_SCHEMA_MATRIX PASS: 6 type/transaction paths; unresolved markers, House required facts, rent amount/period fail closed; optional apartment/land facts preserved")


if __name__ == "__main__":
    main()
    test_gate_a_type_transaction_matrix()
