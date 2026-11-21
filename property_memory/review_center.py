"""Private Review Center domain workflow and Gate A approval."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import threading
import uuid
from typing import Any, Callable
import unicodedata

from .evidence_read_model import AI_INTERNAL_ANALYSIS
from .mutation_service import ReliabilityBoundary, apply_approved_mutation

PENDING_REVIEW = "PENDING_REVIEW"; EDITED = "EDITED"; APPROVING = "APPROVING"; APPROVED = "APPROVED"; REJECTED = "REJECTED"; STALE_REVIEW = "STALE_REVIEW"
CRITICAL_FIELDS = {"title", "price_vnd", "price_vnd_per_month", "price", "price_type", "transaction_type", "price_cycle", "address", "area", "property_type", "house_type", "bedrooms", "bathrooms", "width", "length", "land_area", "construction_area", "road_access", "legal_status"}

def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))

def _property_type(value: Any) -> str | None:
    key = _fold(value).replace("đ", "d")
    return {"apartment": "APARTMENT", "can ho": "APARTMENT", "house": "HOUSE", "nha": "HOUSE", "land": "LAND", "dat": "LAND"}.get(key)

def _transaction_type(facts: dict[str, Any]) -> str | None:
    key = _fold(facts.get("price_type") or facts.get("transaction_type")).replace("đ", "d")
    return {"sale": "SALE", "ban": "SALE", "rent": "RENT", "rental": "RENT", "cho thue": "RENT"}.get(key)

def _marker_set(package: dict[str, Any]) -> set[str]:
    return {_fold(x).replace("đ", "d") for x in package["candidate"].get("missing_fields", [])}

def _has_value(value: Any) -> bool:
    return value is not None and value != ""

_GATE_MARKERS = {
    "title": {"title", "name"},
    "address": {"address", "location", "relative_location"},
    "area": {"area", "general_area"},
    "property_type": {"property_type", "propertytype"},
    "transaction": {"transaction_type", "transactiontype", "price_type"},
    "price_vnd": {"price", "price_vnd", "asking_price_vnd"},
    "price_vnd_per_month": {"price", "price_vnd_per_month", "monthly_price_vnd"},
    "house_type": {"house_type"},
    "bedrooms": {"bedrooms"},
    "bathrooms": {"bathrooms"},
}

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

class ReviewCenter:
    def __init__(self, store: Any, *, reliability: ReliabilityBoundary | None = None):
        self.store = store; self.reliability = reliability; self.packages: dict[str, dict[str, Any]] = {}; self._lock = threading.RLock()
        self._load_packages()

    def _load_packages(self) -> None:
        rows = self.store.connection.execute("SELECT package_json FROM review_packages ORDER BY updated_at, review_id").fetchall()
        for row in rows:
            package = json.loads(row[0])
            self.packages[package["review_id"]] = package

    def _persist(self, package: dict[str, Any]) -> None:
        self.store.connection.execute(
            """INSERT INTO review_packages
               (review_id, candidate_id, intake_id, review_revision, state,
                package_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(review_id) DO UPDATE SET
                review_revision=excluded.review_revision,
                state=excluded.state,
                package_json=excluded.package_json,
                updated_at=excluded.updated_at""",
            (package["review_id"], package["candidate"]["candidate_id"], package["candidate"]["intake_id"], package["review_revision"], package["state"], json.dumps(package, ensure_ascii=False, sort_keys=True), package["created_at"], package["updated_at"]),
        )
        self.store.connection.commit()

    def _claim_approval(self, package: dict[str, Any], *, owner_id: str, idempotency_key: str) -> None:
        previous = package["state"]
        decision = {"type": "APPROVE_INTENT", "owner_id": owner_id, "revision": package["review_revision"], "idempotency_key": idempotency_key, "at": _now()}
        package["state"] = APPROVING
        package["decision"] = decision
        package["updated_at"] = decision["at"]
        try:
            with self.store.connection:
                row = self.store.connection.execute("UPDATE review_packages SET state=?, package_json=?, updated_at=? WHERE review_id=? AND review_revision=? AND state IN (?, ?)" , (APPROVING, json.dumps(package, ensure_ascii=False, sort_keys=True), package["updated_at"], package["review_id"], package["review_revision"], PENDING_REVIEW, EDITED))
                if row.rowcount != 1:
                    raise ValueError("CONCURRENT_APPROVAL")
        except Exception:
            package["state"] = previous
            raise

    def _validate_approval(self, package: dict[str, Any], decision: str) -> None:
        candidate = package["candidate"]
        facts = candidate.get("facts", {})
        markers = _marker_set(package)
        property_type = _property_type(facts.get("property_type"))
        transaction_type = _transaction_type(facts)
        required = [
            ("title", facts.get("title")),
            ("address", facts.get("address")),
            ("area", facts.get("area")),
            ("property_type", facts.get("property_type")),
            ("transaction", transaction_type),
        ]
        if property_type is None and _has_value(facts.get("property_type")):
            raise ValueError("PROPERTY_TYPE_UNSUPPORTED")
        if transaction_type not in {"SALE", "RENT"}:
            required.append(("transaction", None))

        if transaction_type == "RENT":
            amount_field = "price_vnd_per_month"
            cycle = facts.get("price_cycle")
            if cycle not in (None, "") and str(cycle).strip().upper() != "MONTH":
                raise ValueError("RENT_PERIOD_NOT_SUPPORTED_BY_PROPERTY_CORE")
            if not _has_value(cycle) and _has_value(facts.get(amount_field)):
                # This existing fact name and the current public DTO both encode MONTH.
                cycle = "MONTH"
            if not _has_value(cycle) or markers.intersection({"price_cycle", "pricecycle", "rent_cycle"}):
                required.append(("price_cycle", None))
        else:
            amount_field = "price_vnd"
            if _has_value(facts.get("price_cycle")):
                raise ValueError("SALE_PRICE_PERIOD_INVALID")
        required.append((amount_field, facts.get(amount_field)))
        if property_type == "HOUSE":
            required.extend((field, facts.get(field)) for field in ("house_type", "bedrooms", "bathrooms"))

        missing = []
        for field, value in required:
            if not _has_value(value) or markers.intersection(_GATE_MARKERS.get(field, set())):
                missing.append(field)
        amount = facts.get(amount_field)
        if (amount_field in {"price_vnd", "price_vnd_per_month"}
                and _has_value(amount)
                and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0)):
            missing.append(amount_field)
        if missing:
            raise ValueError("MISSING_FIELDS_REQUIRE_OWNER")
        if candidate.get("conflicts") or any(x.get("conflicting_signals") for x in package.get("match_results", [])):
            raise ValueError("CONFLICT_REQUIRES_REVIEW")
        if decision == "CREATE_NEW_PROPERTY" and any(x.get("classification") != "NEW_PROPERTY" for x in package.get("match_results", [])):
            raise ValueError("MATCH_REQUIRES_REVIEW")

    def _existing_for_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return next((x for x in self.packages.values() if x.get("candidate", {}).get("candidate_id") == candidate_id), None)

    def create_intake_package(self, candidate: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist a private pending review without mutating canonical PropertyStore."""
        existing = self._existing_for_candidate(candidate["candidate_id"])
        if existing:
            return deepcopy(existing)
        now = _now()
        digest = hashlib.sha256(candidate["candidate_id"].encode("utf-8")).hexdigest()[:24]
        package = {"classification": AI_INTERNAL_ANALYSIS, "review_id": "review-" + digest, "review_revision": 1, "state": PENDING_REVIEW, "created_at": now, "updated_at": now, "source_bundle_id": "bundle-" + digest, "candidate": deepcopy(candidate), "original_candidate": deepcopy(candidate), "match_results": deepcopy(matches), "owner_edits": [], "property_intelligence": None, "market_snapshot": None, "comparable_set": None, "valuation": None, "internal_analysis": None, "decision": None, "mutation_result": None, "processing_stage": "CANDIDATE_MATCHED"}
        self.packages[package["review_id"]] = package
        self._persist(package)
        return deepcopy(package)

    def refresh_intake_package(self, review_id: str, candidate: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist an extraction revision for an existing, still-private intake review."""
        package = self.packages.get(review_id)
        if not package:
            raise KeyError("REVIEW_NOT_FOUND")
        if package.get("state") in {APPROVED, APPROVING} or package.get("mutation_result"):
            raise ValueError("REVIEW_REVISION_PROTECTED")
        now = _now()
        package["candidate"] = deepcopy(candidate)
        package["match_results"] = deepcopy(matches)
        package["review_revision"] = int(package.get("review_revision") or 0) + 1
        package["state"] = PENDING_REVIEW
        package["updated_at"] = now
        package["processing_stage"] = "AI_EXTRACTION_SUCCEEDED"
        package["decision"] = None
        package["mutation_result"] = None
        self._persist(package)
        return deepcopy(package)

    def create_package(self, candidate: dict[str, Any], property_intelligence: dict[str, Any], market_snapshot: dict[str, Any], comparable_set: dict[str, Any], valuation: dict[str, Any], internal_analysis: dict[str, Any]) -> dict[str, Any]:
        if internal_analysis.get("classification") != AI_INTERNAL_ANALYSIS: raise ValueError("INTERNAL_ANALYSIS_REQUIRED")
        rid = "review-" + uuid.uuid4().hex[:24]
        package = {"classification": AI_INTERNAL_ANALYSIS, "review_id": rid, "review_revision": 1, "state": PENDING_REVIEW, "created_at": _now(), "updated_at": _now(), "candidate": deepcopy(candidate), "original_candidate": deepcopy(candidate), "owner_edits": [], "property_intelligence": deepcopy(property_intelligence), "market_snapshot": deepcopy(market_snapshot), "comparable_set": deepcopy(comparable_set), "valuation": deepcopy(valuation), "internal_analysis": deepcopy(internal_analysis), "decision": None, "mutation_result": None}
        self.packages[rid] = package; self._persist(package); return deepcopy(package)

    def view(self, review_id: str) -> dict[str, Any]:
        if review_id not in self.packages: raise KeyError("REVIEW_NOT_FOUND")
        return deepcopy(self.packages[review_id])

    def remove_test_only(self, intake_id: str) -> str:
        """Remove an unapproved TEST_ONLY review package, idempotently."""
        with self._lock:
            matches = [
                (rid, package) for rid, package in self.packages.items()
                if package.get("candidate", {}).get("intake_id") == intake_id
            ]
            if not matches:
                return "ALREADY_ABSENT"
            for rid, package in matches:
                candidate = package.get("candidate", {})
                raw = candidate.get("raw", {}).get("intake", {}) if isinstance(candidate.get("raw"), dict) else {}
                if candidate.get("source") != "TEST_ONLY" and raw.get("source") != "TEST_ONLY":
                    raise ValueError("REAL_RECORD_PROTECTED")
                if package.get("state") == APPROVED or package.get("mutation_result"):
                    raise ValueError("APPROVED_RECORD_PROTECTED")
                self.store.connection.execute("DELETE FROM review_packages WHERE review_id = ?", (rid,))
                self.store.connection.commit()
                self.packages.pop(rid, None)
            return "REMOVED"

    def edit(self, review_id: str, *, expected_revision: int, field: str, value: Any, owner_id: str) -> dict[str, Any]:
        package = self.packages.get(review_id)
        if not package: raise KeyError("REVIEW_NOT_FOUND")
        if package["review_revision"] != expected_revision: raise ValueError("STALE_REVIEW")
        if not owner_id or value is None: raise ValueError("OWNER_EDIT_METADATA_REQUIRED")
        facts = package["candidate"].setdefault("facts", {})
        canonical_field = {"transaction_type": "price_type", "transactionType": "price_type",
                           "location": "address", "relative_location": "address", "name": "title"}.get(field, field)
        if field == "price":
            canonical_field = "price_vnd_per_month" if _transaction_type(facts) == "RENT" else "price_vnd"
        old = facts.get(canonical_field)
        package["review_revision"] += 1; package["updated_at"] = _now(); package["state"] = STALE_REVIEW if canonical_field in CRITICAL_FIELDS and package.get("internal_analysis") is not None else EDITED
        edit = {"revision": package["review_revision"], "field": canonical_field, "previous_value": deepcopy(old), "new_value": deepcopy(value), "origin": "OWNER_CONFIRMED", "owner_id": owner_id, "edited_at": package["updated_at"]}
        package["owner_edits"].append(edit); facts[canonical_field] = value
        package["candidate"].setdefault("owner_confirmed_facts", {})[canonical_field] = value
        marker_aliases = {
            "title": {"title", "name"}, "address": {"address", "location", "relative_location"},
            "area": {"area", "general_area"}, "property_type": {"property_type", "propertytype"},
            "price_type": {"price_type", "transaction_type", "transactiontype"},
            "price_vnd": {"price", "price_vnd", "asking_price_vnd"},
            "price_vnd_per_month": {"price", "price_vnd_per_month", "monthly_price_vnd"},
            "house_type": {"house_type"}, "bedrooms": {"bedrooms"}, "bathrooms": {"bathrooms"},
            "price_cycle": {"price_cycle", "pricecycle", "rent_cycle"},
        }
        remove = {_fold(x).replace("đ", "d") for x in marker_aliases.get(canonical_field, {canonical_field})}
        package["candidate"]["missing_fields"] = [x for x in package["candidate"].get("missing_fields", []) if _fold(x).replace("đ", "d") not in remove]
        self._persist(package)
        return deepcopy(package)

    def reject(self, review_id: str, *, expected_revision: int, owner_id: str, reason: str) -> dict[str, Any]:
        package = self.packages.get(review_id)
        if not package: raise KeyError("REVIEW_NOT_FOUND")
        if package["review_revision"] != expected_revision: raise ValueError("STALE_REVIEW")
        if not owner_id or not reason: raise ValueError("REJECTION_METADATA_REQUIRED")
        if package["state"] == REJECTED: return deepcopy(package)
        if package["state"] == APPROVED: raise ValueError("REVIEW_ALREADY_APPROVED")
        package["state"] = REJECTED; package["decision"] = {"type": "REJECT", "owner_id": owner_id, "reason": reason, "at": _now(), "revision": expected_revision}; package["updated_at"] = package["decision"]["at"]
        self._persist(package)
        return deepcopy(package)

    def record_public_release(
        self, review_id: str, *, expected_revision: int, property_id: str,
        owner_id: str, action: str, idempotency_key: str, reason: str | None = None,
    ) -> dict[str, Any]:
        """Record a distinct, explicit public-release decision in the existing review package."""
        with self._lock:
            package = self.packages.get(review_id)
            if package is None:
                raise KeyError("REVIEW_NOT_FOUND")
            if package.get("review_revision") != expected_revision:
                raise ValueError("STALE_REVIEW")
            if package.get("state") != APPROVED or package.get("decision", {}).get("type") != "APPROVE":
                raise ValueError("GATE_A_APPROVAL_REQUIRED")
            if package.get("mutation_result", {}).get("property_id") != property_id:
                raise ValueError("PUBLIC_RELEASE_PROPERTY_MISMATCH")
            if not owner_id or not idempotency_key or action not in {"APPROVE_PUBLICATION", "REVOKE_PUBLICATION"}:
                raise ValueError("PUBLIC_RELEASE_METADATA_REQUIRED")

            decisions = package.setdefault("public_release_decisions", [])
            previous = next((x for x in decisions if x.get("idempotency_key") == idempotency_key), None)
            if previous:
                if previous.get("action") != action or previous.get("property_id") != property_id or previous.get("review_revision") != expected_revision or previous.get("owner_id") != owner_id or (previous.get("reason") or None) != (reason or None):
                    raise ValueError("PUBLIC_RELEASE_IDEMPOTENCY_CONFLICT")
                return deepcopy(package)

            prop = self.store.get(property_id)
            if prop is None:
                raise ValueError("PROPERTY_NOT_FOUND")
            property_revision = int(prop.get("property_revision") or 0)
            if property_revision < 1:
                raise ValueError("PROPERTY_REVISION_REQUIRED")
            latest = next((x for x in reversed(decisions) if x.get("property_id") == property_id), None)
            if action == "REVOKE_PUBLICATION" and (
                latest is None or latest.get("action") != "APPROVE_PUBLICATION"
            ):
                raise ValueError("PUBLICATION_NOT_ACTIVE_FOR_REVISION")
            if action == "APPROVE_PUBLICATION" and latest is not None and (
                latest.get("action") == "APPROVE_PUBLICATION" and latest.get("property_revision") == property_revision
            ):
                raise ValueError("PUBLICATION_ALREADY_APPROVED")

            now = _now()
            decisions.append({
                "action": action, "owner_id": owner_id, "property_id": property_id,
                "property_revision": property_revision, "review_id": review_id,
                "review_revision": expected_revision, "idempotency_key": idempotency_key,
                "at": now,
            })
            if reason:
                decisions[-1]["reason"] = str(reason).strip()[:240]
            package["updated_at"] = now
            self._persist(package)
            return deepcopy(package)

    def approve(self, review_id: str, *, expected_revision: int, owner_id: str, decision: str, idempotency_key: str, target_property_id: str | None = None, failure: Callable[[str], None] | None = None) -> dict[str, Any]:
        with self._lock:
            package = self.packages.get(review_id)
            if not package:
                raise KeyError("REVIEW_NOT_FOUND")
            if package["review_revision"] != expected_revision:
                raise ValueError("STALE_REVIEW")
            if package["state"] == APPROVED:
                if package["decision"] and package["decision"].get("idempotency_key") == idempotency_key:
                    return deepcopy(package)
                raise ValueError("REVIEW_ALREADY_APPROVED")
            if package["state"] == APPROVING:
                if not package.get("decision") or package["decision"].get("idempotency_key") != idempotency_key:
                    raise ValueError("CONCURRENT_APPROVAL")
            elif package["state"] in {REJECTED, STALE_REVIEW}:
                raise ValueError("STALE_REVIEW" if package["state"] == STALE_REVIEW else "REVIEW_REJECTED")
            if not owner_id or not idempotency_key or decision not in {"CREATE_NEW_PROPERTY", "ATTACH_TO_EXISTING_PROPERTY"}:
                raise ValueError("APPROVAL_METADATA_REQUIRED")
            previous_state = package["state"]
            if package["state"] != APPROVING:
                self._validate_approval(package, decision)
                self._claim_approval(package, owner_id=owner_id, idempotency_key=idempotency_key)
            try:
                result = apply_approved_mutation(self.store, package["candidate"], decision, idempotency_key=idempotency_key, target_property_id=target_property_id, reliability=self.reliability, failure=failure)
                for edit in package["owner_edits"]:
                    event_id = f"owner-{review_id}-{edit['revision']}"
                    if not self.store.get_field_observation(event_id):
                        self.store.append_field_observation(result["property_id"], {"field_name": edit["field"], "raw_value": edit["new_value"], "normalized_value": edit["new_value"], "information_origin": "OWNER_CONFIRMED", "confirmed_by_owner": True, "appearance_id": result["appearance_id"]}, event_id=event_id)
                package["state"] = APPROVED
                package["decision"] = {"type": "APPROVE", "owner_id": owner_id, "revision": expected_revision, "idempotency_key": idempotency_key, "at": _now()}
                package["mutation_result"] = deepcopy(result)
                package["updated_at"] = package["decision"]["at"]
                self._persist(package)
                return deepcopy(package)
            except Exception:
                package["state"] = previous_state
                package["decision"] = None
                package["mutation_result"] = None
                package["updated_at"] = _now()
                self._persist(package)
                raise

def public_review_projection(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("classification") == AI_INTERNAL_ANALYSIS: raise ValueError("AI_INTERNAL_ANALYSIS_PRIVATE_ONLY")
    return {"id": value.get("id"), "title": value.get("title")} if value.get("id") else {}
