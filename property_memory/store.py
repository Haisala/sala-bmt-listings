"""Small SQLite foundation for canonical properties.

This module is deliberately independent of the HTTP Admin and public JSON
projection. It stores no production record until an explicit later workflow
chooses to call it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROPERTY_SCHEMA_VERSION = 7
LIFECYCLE_STATES = frozenset({
    "RECEIVED", "NORMALIZED", "NEEDS_REVIEW", "DRAFT_READY",
    "REJECTED", "BLOCKED", "FAILED_RETRYABLE", "FAILED_TERMINAL",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_private_db_path() -> Path:
    private_root = Path(os.environ.get(
        "BMT_ADMIN_PRIVATE_ROOT", "/Users/trandinhhieu/BMTPropertyHubPrivate"
    ))
    return private_root / "property-memory.sqlite3"


class PropertyStore:
    """Canonical property persistence with an immutable internal ID."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_private_db_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Admin uses ThreadingHTTPServer; one configured PropertyStore is
        # shared by authenticated request handlers. Domain mutation/review
        # boundaries still own transactions and approval locking.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    def initialize(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS canonical_properties (
                    property_id TEXT PRIMARY KEY,
                    human_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    property_type TEXT,
                    address TEXT,
                    area TEXT,
                    width REAL,
                    length REAL,
                    land_area REAL,
                    construction_area REAL,
                    price REAL,
                    legal_status TEXT,
                    road_access TEXT,
                    floors INTEGER,
                    rooms_json TEXT NOT NULL DEFAULT '[]',
                    interior TEXT,
                    features_json TEXT NOT NULL DEFAULT '[]',
                    factual_description TEXT,
                    lifecycle_state TEXT NOT NULL,
                    property_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    attributes_json TEXT NOT NULL DEFAULT '{}',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    rights_json TEXT NOT NULL DEFAULT '{}',
                    confidence_json TEXT NOT NULL DEFAULT '{}',
                    missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    conflicts_json TEXT NOT NULL DEFAULT '[]',
                    CHECK (lifecycle_state IN (
                        'RECEIVED','NORMALIZED','NEEDS_REVIEW','DRAFT_READY',
                        'REJECTED','BLOCKED','FAILED_RETRYABLE','FAILED_TERMINAL'
                    ))
                );
            """)
            current = self.connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'property_schema_version'"
            ).fetchone()
            if current is None:
                self.connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('property_schema_version', ?)",
                    (str(PROPERTY_SCHEMA_VERSION),),
                )
            elif int(current[0]) > PROPERTY_SCHEMA_VERSION:
                raise RuntimeError("UNSUPPORTED_PROPERTY_SCHEMA_VERSION")
            if current is None or int(current[0]) < 2:
                self._upgrade_to_v2()
            if current is None or int(current[0]) < 3:
                self._upgrade_to_v3()
            if current is None or int(current[0]) < 4:
                self._upgrade_to_v4()
            if current is None or int(current[0]) < 5:
                self._upgrade_to_v5()
            if current is None or int(current[0]) < 6:
                self._upgrade_to_v6()
            if current is None or int(current[0]) < 7:
                self._upgrade_to_v7()

    def _upgrade_to_v2(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS source_appearances (
                    appearance_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    source_id TEXT, source_class TEXT NOT NULL CHECK (source_class IN ('PERSONAL','COMPANY','UNKNOWN')),
                    source_type TEXT, source_group TEXT, intake_id TEXT, received_at TEXT NOT NULL,
                    source_timestamp TEXT, rights_status TEXT NOT NULL CHECK (rights_status IN ('PERSONAL','COMPANY','UNKNOWN')),
                    processing_metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    UNIQUE(property_id, appearance_id)
                );
                CREATE TABLE IF NOT EXISTS provenance_refs (
                    provenance_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    appearance_id TEXT NOT NULL REFERENCES source_appearances(appearance_id) ON DELETE RESTRICT,
                    field_name TEXT, information_origin TEXT NOT NULL CHECK (information_origin IN
                    ('SOURCE_RAW','NORMALIZED','AI_EXTRACTED','AI_INFERRED','OWNER_CONFIRMED')),
                    intake_id TEXT, media_ref TEXT, media_sha256 TEXT, confidence REAL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rights_refs (
                    rights_id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    appearance_id TEXT REFERENCES source_appearances(appearance_id) ON DELETE RESTRICT,
                    rights_status TEXT NOT NULL CHECK (rights_status IN ('PERSONAL','COMPANY','UNKNOWN')),
                    channel TEXT, approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0,1)), created_at TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '2')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """)

    def _upgrade_to_v3(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS property_field_history (
                    event_id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    field_name TEXT NOT NULL, raw_value_json TEXT, normalized_value_json TEXT,
                    information_origin TEXT NOT NULL CHECK (information_origin IN ('SOURCE_RAW','NORMALIZED','AI_EXTRACTED','AI_INFERRED','OWNER_CONFIRMED')),
                    appearance_id TEXT REFERENCES source_appearances(appearance_id) ON DELETE RESTRICT,
                    provenance_id TEXT REFERENCES provenance_refs(provenance_id) ON DELETE RESTRICT,
                    confidence REAL, confirmed_by_owner INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_by_owner IN (0,1)),
                    observed_at TEXT, recorded_at TEXT NOT NULL, conflict_status TEXT NOT NULL DEFAULT 'NONE'
                    CHECK (conflict_status IN ('NONE','OPEN','RESOLVED','DISMISSED'))
                );
                CREATE TABLE IF NOT EXISTS property_price_history (
                    event_id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    amount_vnd INTEGER NOT NULL CHECK (amount_vnd > 0), currency TEXT NOT NULL DEFAULT 'VND',
                    appearance_id TEXT REFERENCES source_appearances(appearance_id) ON DELETE RESTRICT,
                    provenance_id TEXT REFERENCES provenance_refs(provenance_id) ON DELETE RESTRICT,
                    information_origin TEXT NOT NULL CHECK (information_origin IN ('SOURCE_RAW','NORMALIZED','AI_EXTRACTED','AI_INFERRED','OWNER_CONFIRMED')),
                    observed_at TEXT, recorded_at TEXT NOT NULL, confidence REAL,
                    confirmed_by_owner INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_by_owner IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS property_media_history (
                    event_id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    appearance_id TEXT REFERENCES source_appearances(appearance_id) ON DELETE RESTRICT,
                    media_ref TEXT NOT NULL, media_sha256 TEXT, role TEXT, observed_at TEXT, recorded_at TEXT NOT NULL,
                    rights_status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (rights_status IN ('PERSONAL','COMPANY','UNKNOWN')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS property_conflicts (
                    conflict_id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES canonical_properties(property_id) ON DELETE RESTRICT,
                    field_name TEXT NOT NULL, competing_event_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','DISMISSED')),
                    created_at TEXT NOT NULL, resolved_at TEXT, resolution_notes TEXT
                );
                INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '3')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """)

    def _upgrade_to_v4(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS match_candidates (
                    match_candidate_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    property_id TEXT,
                    classification TEXT NOT NULL CHECK (classification IN
                    ('NEW_PROPERTY','POSSIBLE_DUPLICATE','EXISTING_PROPERTY_NEW_INFORMATION')),
                    score INTEGER NOT NULL, matched_signals_json TEXT NOT NULL,
                    differing_signals_json TEXT NOT NULL, missing_signals_json TEXT NOT NULL,
                    conflicting_signals_json TEXT NOT NULL, explanation TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(candidate_id, property_id, algorithm_version)
                );
                INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '4')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """)

    def _upgrade_to_v5(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS property_mutation_operations (
                    operation_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    candidate_id TEXT NOT NULL, decision TEXT NOT NULL, target_property_id TEXT,
                    status TEXT NOT NULL CHECK (status IN ('accepted','retrying','succeeded','failed','terminal')),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '5')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """)

    def _upgrade_to_v6(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS review_packages (
                    review_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    intake_id TEXT NOT NULL,
                    review_revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    package_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_review_packages_state
                    ON review_packages(state, updated_at);
                INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '6')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """)

    def _upgrade_to_v7(self) -> None:
        with self.connection:
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(canonical_properties)").fetchall()}
            if "property_revision" not in columns:
                self.connection.execute("ALTER TABLE canonical_properties ADD COLUMN property_revision INTEGER NOT NULL DEFAULT 0")
            self.connection.execute("INSERT INTO schema_meta(key, value) VALUES('property_schema_version', '7') ON CONFLICT(key) DO UPDATE SET value = excluded.value")

    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'property_schema_version'"
        ).fetchone()
        return int(row[0])

    def create(self, values: dict[str, Any], *, property_id: str | None = None) -> dict[str, Any]:
        now = utc_now()
        property_id = property_id or "property-" + uuid.uuid4().hex
        state = values.get("lifecycle_state", "RECEIVED")
        if state not in LIFECYCLE_STATES:
            raise ValueError("INVALID_LIFECYCLE_STATE")
        record = self._record(values, property_id, now, now, state)
        columns = ", ".join(record)
        placeholders = ", ".join("?" for _ in record)
        try:
            with self.connection:
                self.connection.execute(
                    f"INSERT INTO canonical_properties ({columns}) VALUES ({placeholders})",
                    tuple(record.values()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("DUPLICATE_PROPERTY_IDENTITY") from exc
        return self.get(property_id)  # type: ignore[return-value]

    def get(self, property_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM canonical_properties WHERE property_id = ?", (property_id,)
        ).fetchone()
        return self._decode(row) if row else None

    def list_properties(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM canonical_properties ORDER BY property_id").fetchall()
        return [self._decode(row) for row in rows]

    def purge_test_only_property(self, property_id: str, *, owner_intent: str, environment: str) -> str:
        """Idempotently purge only an approved TEST_ONLY property in staging."""
        if environment != "staging":
            raise ValueError("WRONG_ENVIRONMENT")
        if owner_intent != "APPROVED_TEST_ONLY_PURGE":
            raise ValueError("OWNER_PURGE_INTENT_REQUIRED")
        if not property_id or not property_id.startswith("property-"):
            raise ValueError("INVALID_PROPERTY_ID")
        row = self.connection.execute("SELECT property_id FROM canonical_properties WHERE property_id = ?", (property_id,)).fetchone()
        if row is None:
            return "ALREADY_ABSENT"
        marker = self.connection.execute(
            "SELECT 1 FROM source_appearances WHERE property_id = ? AND source_id = 'TEST_ONLY' LIMIT 1",
            (property_id,),
        ).fetchone()
        if marker is None:
            raise ValueError("TEST_ONLY_PROVENANCE_REQUIRED")
        with self.connection:
            for table in (
                "property_field_history", "property_price_history", "property_media_history",
                "property_conflicts", "provenance_refs", "rights_refs", "source_appearances",
                "property_mutation_operations", "match_candidates",
            ):
                try:
                    self.connection.execute(f"DELETE FROM {table} WHERE property_id = ?", (property_id,))
                except Exception:
                    continue
            self.connection.execute("DELETE FROM canonical_properties WHERE property_id = ?", (property_id,))
        return "PURGED"

    def save_match_candidate(self, result: dict[str, Any]) -> dict[str, Any]:
        with self.connection:
            self.connection.execute("""INSERT INTO match_candidates
            (match_candidate_id, candidate_id, property_id, classification, score, matched_signals_json,
             differing_signals_json, missing_signals_json, conflicting_signals_json, explanation,
             algorithm_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET
             classification=excluded.classification, score=excluded.score,
             matched_signals_json=excluded.matched_signals_json, differing_signals_json=excluded.differing_signals_json,
             missing_signals_json=excluded.missing_signals_json, conflicting_signals_json=excluded.conflicting_signals_json,
             explanation=excluded.explanation""", (result["match_candidate_id"], result["candidate_id"], result.get("property_id"), result["classification"], result["score"], json.dumps(result["matched_signals"], ensure_ascii=False, sort_keys=True), json.dumps(result["differing_signals"], ensure_ascii=False, sort_keys=True), json.dumps(result["missing_signals"], ensure_ascii=False, sort_keys=True), json.dumps(result["conflicting_signals"], ensure_ascii=False, sort_keys=True), result["explanation"], result["algorithm_version"], result["created_at"]))
        return result

    def update(self, property_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get(property_id)
        if current is None:
            raise KeyError("PROPERTY_NOT_FOUND")
        state = changes.get("lifecycle_state", current["lifecycle_state"])
        if state not in LIFECYCLE_STATES:
            raise ValueError("INVALID_LIFECYCLE_STATE")
        merged = {**current, **changes}
        merged.pop("property_id", None)
        merged["updated_at"] = utc_now()
        record = self._record(merged, property_id, current["created_at"], merged["updated_at"], state)
        assignments = ", ".join(f"{key} = ?" for key in record if key != "property_id")
        values = [record[key] for key in record if key != "property_id"] + [property_id]
        with self.connection:
            self.connection.execute(
                f"UPDATE canonical_properties SET {assignments} WHERE property_id = ?", values
            )
        return self.get(property_id)  # type: ignore[return-value]

    def close(self) -> None:
        self.connection.close()

    def create_source_appearance(self, property_id: str, values: dict[str, Any], *, appearance_id: str | None = None) -> dict[str, Any]:
        if self.get(property_id) is None:
            raise KeyError("PROPERTY_NOT_FOUND")
        appearance_id = appearance_id or "appearance-" + uuid.uuid4().hex
        source_class = values.get("source_class", "UNKNOWN")
        rights_status = values.get("rights_status", "UNKNOWN")
        if source_class not in {"PERSONAL", "COMPANY", "UNKNOWN"}:
            raise ValueError("INVALID_SOURCE_CLASS")
        if rights_status not in {"PERSONAL", "COMPANY", "UNKNOWN"}:
            raise ValueError("INVALID_RIGHTS_STATUS")
        now = utc_now()
        record = (appearance_id, property_id, values.get("source_id"), source_class, values.get("source_type"),
                  values.get("source_group"), values.get("intake_id"), values.get("received_at", now),
                  values.get("source_timestamp"), rights_status,
                  json.dumps(values.get("processing_metadata", {}), ensure_ascii=False, sort_keys=True), now)
        try:
            with self.connection:
                self.connection.execute("""INSERT INTO source_appearances
                (appearance_id, property_id, source_id, source_class, source_type, source_group, intake_id,
                 received_at, source_timestamp, rights_status, processing_metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", record)
        except sqlite3.IntegrityError as exc:
            raise ValueError("DUPLICATE_APPEARANCE_IDENTITY") from exc
        return self.get_source_appearance(appearance_id)  # type: ignore[return-value]

    def get_source_appearance(self, appearance_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM source_appearances WHERE appearance_id = ?", (appearance_id,)).fetchone()
        return self._decode_appearance(row) if row else None

    def list_source_appearances(self, property_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM source_appearances WHERE property_id = ? ORDER BY created_at, appearance_id", (property_id,)).fetchall()
        return [self._decode_appearance(row) for row in rows]

    def add_provenance(self, property_id: str, appearance_id: str, values: dict[str, Any], *, provenance_id: str | None = None) -> dict[str, Any]:
        if self.get(property_id) is None:
            raise KeyError("PROPERTY_NOT_FOUND")
        appearance = self.get_source_appearance(appearance_id)
        if appearance is None or appearance["property_id"] != property_id:
            raise KeyError("APPEARANCE_NOT_FOUND")
        origin = values.get("information_origin", "SOURCE_RAW")
        if origin not in {"SOURCE_RAW", "NORMALIZED", "AI_EXTRACTED", "AI_INFERRED", "OWNER_CONFIRMED"}:
            raise ValueError("INVALID_INFORMATION_ORIGIN")
        provenance_id = provenance_id or "provenance-" + uuid.uuid4().hex
        now = utc_now()
        record = (provenance_id, property_id, appearance_id, values.get("field_name"), origin,
                  values.get("intake_id", appearance.get("intake_id")), values.get("media_ref"),
                  values.get("media_sha256"), values.get("confidence"), now)
        try:
            with self.connection:
                self.connection.execute("""INSERT INTO provenance_refs
                (provenance_id, property_id, appearance_id, field_name, information_origin, intake_id,
                 media_ref, media_sha256, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", record)
        except sqlite3.IntegrityError as exc:
            raise ValueError("DUPLICATE_PROVENANCE_IDENTITY") from exc
        return self.get_provenance(provenance_id)  # type: ignore[return-value]

    def get_provenance(self, provenance_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM provenance_refs WHERE provenance_id = ?", (provenance_id,)).fetchone()
        return dict(row) if row else None

    def list_provenance(self, property_id: str, appearance_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM provenance_refs WHERE property_id = ?"
        args: list[Any] = [property_id]
        if appearance_id:
            query += " AND appearance_id = ?"; args.append(appearance_id)
        query += " ORDER BY created_at"
        return [dict(row) for row in self.connection.execute(query, args).fetchall()]

    def add_rights(self, property_id: str, values: dict[str, Any], *, rights_id: str | None = None) -> dict[str, Any]:
        if self.get(property_id) is None:
            raise KeyError("PROPERTY_NOT_FOUND")
        appearance_id = values.get("appearance_id")
        if appearance_id and (self.get_source_appearance(appearance_id) or {}).get("property_id") != property_id:
            raise KeyError("APPEARANCE_NOT_FOUND")
        status = values.get("rights_status", "UNKNOWN")
        if status not in {"PERSONAL", "COMPANY", "UNKNOWN"}:
            raise ValueError("INVALID_RIGHTS_STATUS")
        rights_id = rights_id or "rights-" + uuid.uuid4().hex
        with self.connection:
            self.connection.execute("""INSERT INTO rights_refs
            (rights_id, property_id, appearance_id, rights_status, channel, approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (rights_id, property_id, appearance_id, status,
                                                values.get("channel"), int(bool(values.get("approved", False))), utc_now()))
        return dict(self.connection.execute("SELECT * FROM rights_refs WHERE rights_id = ?", (rights_id,)).fetchone())

    def _require_property(self, property_id: str) -> None:
        if self.get(property_id) is None:
            raise KeyError("PROPERTY_NOT_FOUND")

    def _require_appearance(self, appearance_id: str | None) -> None:
        if appearance_id is not None and self.get_source_appearance(appearance_id) is None:
            raise KeyError("APPEARANCE_NOT_FOUND")

    @staticmethod
    def _require_origin(origin: str) -> None:
        if origin not in {"SOURCE_RAW", "NORMALIZED", "AI_EXTRACTED", "AI_INFERRED", "OWNER_CONFIRMED"}:
            raise ValueError("INVALID_INFORMATION_ORIGIN")

    @staticmethod
    def _json_value(value: Any) -> str | None:
        return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)

    def append_field_observation(self, property_id: str, values: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
        self._require_property(property_id); self._require_appearance(values.get("appearance_id"))
        if not values.get("field_name"): raise ValueError("FIELD_NAME_REQUIRED")
        origin = values.get("information_origin", "SOURCE_RAW"); self._require_origin(origin)
        event_id = event_id or "field-" + uuid.uuid4().hex
        row = (event_id, property_id, values["field_name"], self._json_value(values.get("raw_value")), self._json_value(values.get("normalized_value")), origin, values.get("appearance_id"), values.get("provenance_id"), values.get("confidence"), int(bool(values.get("confirmed_by_owner", False))), values.get("observed_at"), utc_now(), values.get("conflict_status", "NONE"))
        with self.connection:
            self.connection.execute("""INSERT INTO property_field_history
            (event_id, property_id, field_name, raw_value_json, normalized_value_json, information_origin,
             appearance_id, provenance_id, confidence, confirmed_by_owner, observed_at, recorded_at, conflict_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        return self.get_field_observation(event_id)  # type: ignore[return-value]

    def get_field_observation(self, event_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM property_field_history WHERE event_id = ?", (event_id,)).fetchone()
        return self._decode_history(row) if row else None

    def list_field_history(self, property_id: str, field_name: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM property_field_history WHERE property_id = ?"; args: list[Any] = [property_id]
        if field_name: query += " AND field_name = ?"; args.append(field_name)
        query += " ORDER BY recorded_at, event_id"
        return [self._decode_history(row) for row in self.connection.execute(query, args).fetchall()]

    def append_price_observation(self, property_id: str, values: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
        self._require_property(property_id); self._require_appearance(values.get("appearance_id"))
        amount = values.get("amount_vnd")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0: raise ValueError("INVALID_VND_AMOUNT")
        origin = values.get("information_origin", "SOURCE_RAW"); self._require_origin(origin)
        event_id = event_id or "price-" + uuid.uuid4().hex
        row = (event_id, property_id, amount, values.get("currency", "VND"), values.get("appearance_id"), values.get("provenance_id"), origin, values.get("observed_at"), utc_now(), values.get("confidence"), int(bool(values.get("confirmed_by_owner", False))))
        with self.connection:
            self.connection.execute("""INSERT INTO property_price_history
            (event_id, property_id, amount_vnd, currency, appearance_id, provenance_id, information_origin,
             observed_at, recorded_at, confidence, confirmed_by_owner) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        return dict(self.connection.execute("SELECT * FROM property_price_history WHERE event_id = ?", (event_id,)).fetchone())

    def list_price_history(self, property_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM property_price_history WHERE property_id = ? ORDER BY recorded_at, event_id", (property_id,)).fetchall()]

    def append_media_observation(self, property_id: str, values: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
        self._require_property(property_id); self._require_appearance(values.get("appearance_id"))
        if not values.get("media_ref"): raise ValueError("MEDIA_REFERENCE_REQUIRED")
        if values.get("rights_status", "UNKNOWN") not in {"PERSONAL", "COMPANY", "UNKNOWN"}: raise ValueError("INVALID_RIGHTS_STATUS")
        event_id = event_id or "media-" + uuid.uuid4().hex
        row = (event_id, property_id, values.get("appearance_id"), values["media_ref"], values.get("media_sha256"), values.get("role"), values.get("observed_at"), utc_now(), values.get("rights_status", "UNKNOWN"), int(bool(values.get("active", True))))
        with self.connection:
            self.connection.execute("""INSERT INTO property_media_history
            (event_id, property_id, appearance_id, media_ref, media_sha256, role, observed_at, recorded_at, rights_status, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        return dict(self.connection.execute("SELECT * FROM property_media_history WHERE event_id = ?", (event_id,)).fetchone())

    def list_media_history(self, property_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM property_media_history WHERE property_id = ? ORDER BY recorded_at, event_id", (property_id,)).fetchall()]

    def create_conflict(self, property_id: str, field_name: str, event_ids: list[str], *, conflict_id: str | None = None, notes: str | None = None) -> dict[str, Any]:
        self._require_property(property_id)
        if not event_ids: raise ValueError("CONFLICT_EVENTS_REQUIRED")
        conflict_id = conflict_id or "conflict-" + uuid.uuid4().hex
        with self.connection:
            self.connection.execute("""INSERT INTO property_conflicts
            (conflict_id, property_id, field_name, competing_event_ids_json, status, created_at, resolution_notes)
            VALUES (?, ?, ?, ?, 'OPEN', ?, ?)""", (conflict_id, property_id, field_name, json.dumps(event_ids), utc_now(), notes))
        return self.get_conflict(conflict_id)  # type: ignore[return-value]

    def get_conflict(self, conflict_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM property_conflicts WHERE conflict_id = ?", (conflict_id,)).fetchone()
        if not row: return None
        result = dict(row); result["competing_event_ids"] = json.loads(result.pop("competing_event_ids_json")); return result

    def list_conflicts(self, property_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT conflict_id FROM property_conflicts WHERE property_id = ? ORDER BY created_at, conflict_id", (property_id,)).fetchall()
        return [self.get_conflict(row[0]) for row in rows]  # type: ignore[misc]

    def resolve_conflict(self, conflict_id: str, status: str, notes: str) -> dict[str, Any]:
        if status not in {"RESOLVED", "DISMISSED"}: raise ValueError("INVALID_CONFLICT_STATUS")
        with self.connection:
            self.connection.execute("UPDATE property_conflicts SET status = ?, resolved_at = ?, resolution_notes = ? WHERE conflict_id = ?", (status, utc_now(), notes, conflict_id))
        result = self.get_conflict(conflict_id)
        if result is None: raise KeyError("CONFLICT_NOT_FOUND")
        return result

    @staticmethod
    def _decode_history(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for column, key in (("raw_value_json", "raw_value"), ("normalized_value_json", "normalized_value")):
            result[key] = None if result[column] is None else json.loads(result.pop(column))
        return result

    @staticmethod
    def _record(values: dict[str, Any], property_id: str, created_at: str,
                updated_at: str, state: str) -> dict[str, Any]:
        def dumped(key: str, default: Any) -> str:
            return json.dumps(values.get(key, default), ensure_ascii=False, sort_keys=True)
        return {
            "property_id": property_id,
            "human_id": values.get("human_id"), "title": values.get("title", ""),
            "property_type": values.get("property_type"), "address": values.get("address"),
            "area": values.get("area"), "width": values.get("width"), "length": values.get("length"),
            "land_area": values.get("land_area"), "construction_area": values.get("construction_area"),
            "price": values.get("price"), "legal_status": values.get("legal_status"),
            "road_access": values.get("road_access"), "floors": values.get("floors"),
            "rooms_json": dumped("rooms", []), "interior": values.get("interior"),
            "features_json": dumped("features", []), "factual_description": values.get("factual_description"),
            "lifecycle_state": state, "property_revision": int(values.get("property_revision", 0)), "created_at": created_at, "updated_at": updated_at,
            "schema_version": PROPERTY_SCHEMA_VERSION, "attributes_json": dumped("attributes", {}),
            "provenance_json": dumped("provenance", {}), "rights_json": dumped("rights", {}),
            "confidence_json": dumped("confidence", {}), "missing_fields_json": dumped("missing_fields", []),
            "conflicts_json": dumped("conflicts", []),
        }

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for column, key in (("rooms_json", "rooms"), ("features_json", "features"),
                            ("attributes_json", "attributes"), ("provenance_json", "provenance"),
                            ("rights_json", "rights"), ("confidence_json", "confidence"),
                            ("missing_fields_json", "missing_fields"), ("conflicts_json", "conflicts")):
            result[key] = json.loads(result.pop(column))
        return result

    @staticmethod
    def _decode_appearance(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["processing_metadata"] = json.loads(result.pop("processing_metadata_json"))
        return result


def is_publishable_by_rights(rights_status: str | None, *, approved: bool = False) -> bool:
    return rights_status in {"PERSONAL", "COMPANY"} and approved
