"""SALA BĐS Customer Web v2: isolated SQLite-backed review and public read API."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import time
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
try:  # support both ``python sala_web.py`` and package imports in tests
    from .analytics_relay import AnalyticsRelay, AnalyticsRelayError
    from .projection_reader import ProjectionUnavailable, PublicProjectionReader
except ImportError:
    from analytics_relay import AnalyticsRelay, AnalyticsRelayError
    from projection_reader import ProjectionUnavailable, PublicProjectionReader

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
MAX_REQUEST = 10 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024
MAX_FILES = 8
PBKDF2_ROUNDS = 310_000
COOKIE_NAME = "sala_owner_session"
TEST_NAMESPACE_PREFIX = "/__issue39_test__"
ASSET_MAP = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
}


def read_owner_password_file(path: str | Path) -> str:
    """Read the existing SALA BĐS Owner verifier source without logging/copying it."""
    source = Path(path)
    if source.is_symlink():
        raise AuthError("Owner password source must not be a symlink")
    parent = source.parent
    parent_info = parent.stat()
    if (parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) & 0o077):
        raise AuthError("Owner password directory must be private and service-user-owned")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise AuthError("Owner password source is unavailable") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise AuthError("Owner password source must be a private regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise AuthError("Owner password source is invalid")
        try:
            password = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise AuthError("Owner password source is invalid") from exc
        if not password or "\x00" in password:
            raise AuthError("Owner password source is empty or invalid")
        return password
    finally:
        os.close(fd)
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class InputError(ValueError):
    pass


class DuplicateError(ValueError):
    pass


class AuthError(ValueError):
    pass


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str]


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.strip().casefold())
    value = value.replace("đ", "d")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _valid_image(mime: str, data: bytes) -> bool:
    return (
        mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")
        or mime == "image/webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], list[tuple[str, str, str, bytes]]]:
    if not content_type.lower().startswith("multipart/form-data;"):
        raise InputError("Content-Type must be multipart/form-data")
    try:
        wrapper = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + body
    except UnicodeEncodeError as exc:
        raise InputError("Malformed multipart headers") from exc
    msg = BytesParser(policy=policy.default).parsebytes(wrapper)
    if not msg.is_multipart():
        raise InputError("Malformed multipart body")
    fields: dict[str, str] = {}
    files: list[tuple[str, str, str, bytes]] = []
    for part in msg.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            raise InputError("Multipart field has no name")
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is None:
            try:
                fields[name] = payload.decode(part.get_content_charset() or "utf-8").strip()
            except UnicodeDecodeError as exc:
                raise InputError("Text fields must use UTF-8") from exc
        else:
            if name != "media":
                raise InputError("Image files must use the media field")
            safe_name = Path(filename.replace("\\", "/")).name
            if len(payload) > MAX_FILE:
                raise InputError("Each image must be at most 4 MiB")
            mime = (part.get_content_type() or "").lower()
            if mime not in MIME_EXT or not _valid_image(mime, payload):
                raise InputError("Only valid JPEG, PNG, or WebP images are accepted")
            files.append((name, safe_name, mime, payload))
    if len(files) > MAX_FILES:
        raise InputError("At most 8 images are accepted")
    if sum(len(item[3]) for item in files) > MAX_REQUEST:
        raise InputError("Combined images exceed the request limit")
    return fields, files


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            parent = Path(self.path).parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
                raise PermissionError("Customer Web SQLite directory must be private and owned by the service user")
        self.initialize()
        if self.path != ":memory:":
            Path(self.path).chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL);
              INSERT INTO schema_meta(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
              CREATE TABLE IF NOT EXISTS properties(
                property_id TEXT PRIMARY KEY, property_key TEXT NOT NULL UNIQUE,
                property_type TEXT NOT NULL, location TEXT NOT NULL, created_at INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS listings(
                listing_id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES properties(property_id),
                transaction_type TEXT NOT NULL CHECK(transaction_type IN ('SALE','RENT')),
                status TEXT NOT NULL CHECK(status IN ('PENDING_REVIEW','APPROVED','REJECTED')),
                created_at INTEGER NOT NULL, UNIQUE(property_id, transaction_type)
              );
              CREATE TABLE IF NOT EXISTS revisions(
                revision_id TEXT PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(listing_id),
                revision_no INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
                price_vnd INTEGER NOT NULL CHECK(price_vnd > 0), area_m2 REAL NOT NULL CHECK(area_m2 > 0),
                source TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('PENDING_REVIEW','APPROVED','REJECTED')),
                created_at INTEGER NOT NULL, UNIQUE(listing_id, revision_no)
              );
              CREATE TABLE IF NOT EXISTS media(
                media_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                filename TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, bytes BLOB NOT NULL,
                created_at INTEGER NOT NULL, UNIQUE(revision_id, sha256)
              );
              CREATE TABLE IF NOT EXISTS provenance(
                event_id TEXT PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(listing_id),
                revision_id TEXT NOT NULL REFERENCES revisions(revision_id), event TEXT NOT NULL,
                actor TEXT NOT NULL, source TEXT NOT NULL, created_at INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS owner_auth(
                username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL, rounds INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES owner_auth(username),
                expires_at INTEGER NOT NULL
              );
              CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
              CREATE INDEX IF NOT EXISTS idx_revisions_listing ON revisions(listing_id, revision_no DESC);
            """)
            version = db.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()[0]
            if version != 1:
                raise RuntimeError("Unsupported Customer Web database schema version")

    def provision_owner(self, password: str | None, *, allow_existing_short: bool = False, refresh: bool = False) -> bool:
        if not password:
            return False
        if len(password) < 16 and not allow_existing_short:
            raise AuthError("Owner password must contain at least 16 characters")
        with self.connection() as db:
            current = db.execute("SELECT 1 FROM owner_auth WHERE username='owner'").fetchone()
            if current and not refresh:
                return True
            salt = secrets.token_bytes(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
            if current:
                db.execute("UPDATE owner_auth SET salt=?,password_hash=?,rounds=? WHERE username='owner'", (salt, digest, PBKDF2_ROUNDS))
            else:
                db.execute("INSERT INTO owner_auth VALUES('owner',?,?,?)", (salt, digest, PBKDF2_ROUNDS))
        return True

    def authenticate(self, password: str | None) -> str:
        if not password:
            raise AuthError("Owner authentication is not configured")
        with self.connection() as db:
            row = db.execute("SELECT salt,password_hash,rounds FROM owner_auth WHERE username='owner'").fetchone()
            if not row:
                raise AuthError("Owner authentication is not configured")
            check = hashlib.pbkdf2_hmac("sha256", password.encode(), row["salt"], row["rounds"])
            if not hmac.compare_digest(check, row["password_hash"]):
                raise AuthError("Invalid credentials")
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash, "owner", int(time.time()) + 8 * 3600))
            return token

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute("SELECT expires_at FROM sessions WHERE token_hash=? AND username='owner'", (key,)).fetchone()
            if not row:
                return False
            if row["expires_at"] <= int(time.time()):
                db.execute("DELETE FROM sessions WHERE token_hash=?", (key,))
                return False
            return True

    def revoke(self, token: str | None) -> None:
        if token:
            with self.connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def _latest(db: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
        return db.execute("""SELECT r.*, l.property_id,l.transaction_type,l.status AS listing_status
          FROM revisions r JOIN listings l USING(listing_id)
          WHERE l.listing_id=? ORDER BY r.revision_no DESC LIMIT 1""", (listing_id,)).fetchone()

    @staticmethod
    def _validate_fields(fields: dict[str, str]) -> dict:
        required = ("transaction_type", "property_type", "title", "description", "price_vnd", "area_m2", "location", "source")
        if any(not fields.get(k, "").strip() for k in required):
            raise InputError("All required fields must be filled")
        transaction = fields["transaction_type"].upper()
        if transaction not in {"SALE", "RENT"}:
            raise InputError("transaction_type must be SALE or RENT")
        values = {
            "transaction": transaction,
            "property_type": fields["property_type"].strip()[:80],
            "title": fields["title"].strip()[:160],
            "description": fields["description"].strip()[:4000],
            "location": fields["location"].strip()[:300],
            "source": fields["source"].strip()[:120],
        }
        if not values["title"] or not values["description"] or not values["location"]:
            raise InputError("Title, description, and location cannot be empty")
        try:
            values["price"] = int(fields["price_vnd"])
            values["area"] = float(fields["area_m2"])
        except (TypeError, ValueError) as exc:
            raise InputError("Price and area must be numeric") from exc
        if values["price"] <= 0 or values["price"] > 10**15 or not math.isfinite(values["area"]) or values["area"] <= 0 or values["area"] > 1_000_000:
            raise InputError("Price and area must be positive and within supported limits")
        values["property_key"] = hashlib.sha256("|".join((normalize(values["property_type"]), normalize(values["location"]))).encode()).hexdigest()
        return values

    def create_intake(self, fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> dict:
        v = self._validate_fields(fields)
        now = int(time.time())
        listing_id = "lst-" + uuid.uuid4().hex
        revision_id = "rev-" + uuid.uuid4().hex
        try:
            with self.connection() as db:
                row = db.execute("SELECT property_id FROM properties WHERE property_key=?", (v["property_key"],)).fetchone()
                if row:
                    property_id = row["property_id"]
                else:
                    property_id = "prp-" + uuid.uuid4().hex
                    db.execute("INSERT INTO properties VALUES(?,?,?,?,?)", (property_id, v["property_key"], v["property_type"], v["location"], now))
                db.execute("INSERT INTO listings VALUES(?,?,?,?,?)", (listing_id, property_id, v["transaction"], "PENDING_REVIEW", now))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)", (revision_id, listing_id, 1, v["title"], v["description"], v["price"], v["area"], v["source"], "PENDING_REVIEW", now))
                db.execute("INSERT INTO provenance VALUES(?,?,?,?,?,?,?)", ("evt-" + uuid.uuid4().hex, listing_id, revision_id, "INTAKE_CREATED", "owner", v["source"], now))
                for _, filename, mime, blob in files:
                    if not _valid_image(mime, blob):
                        raise InputError("Invalid image bytes")
                    digest = hashlib.sha256(blob).hexdigest()
                    db.execute("INSERT OR IGNORE INTO media VALUES(?,?,?,?,?,?,?)", ("med-" + uuid.uuid4().hex, revision_id, filename, mime, digest, blob, now))
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("A listing for this property and transaction already exists") from exc
        return {"listing_id": listing_id, "property_id": property_id, "revision_id": revision_id, "state": "PENDING_REVIEW", "file_count": len(files)}

    def create_revision(self, listing_id: str, fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> dict:
        v = self._validate_fields(fields)
        now = int(time.time())
        revision_id = "rev-" + uuid.uuid4().hex
        try:
            with self.connection() as db:
                row = db.execute("SELECT l.property_id,l.transaction_type,p.property_type,p.location FROM listings l JOIN properties p USING(property_id) WHERE listing_id=?", (listing_id,)).fetchone()
                if not row:
                    raise KeyError("Listing not found")
                if row["transaction_type"] != v["transaction"]:
                    raise InputError("Transaction type cannot change; create a separate listing")
                if normalize(row["property_type"]) != normalize(v["property_type"]) or normalize(row["location"]) != normalize(v["location"]):
                    raise InputError("Property type and location identify the property; create a separate listing to change them")
                revision_no = db.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM revisions WHERE listing_id=?", (listing_id,)).fetchone()[0]
                prior_revision = db.execute("SELECT revision_id FROM revisions WHERE listing_id=? ORDER BY revision_no DESC LIMIT 1", (listing_id,)).fetchone()
                prior_media = [] if files or not prior_revision else db.execute("SELECT filename,mime,sha256,bytes FROM media WHERE revision_id=?", (prior_revision["revision_id"],)).fetchall()
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)", (revision_id, listing_id, revision_no, v["title"], v["description"], v["price"], v["area"], v["source"], "PENDING_REVIEW", now))
                db.execute("UPDATE listings SET status='PENDING_REVIEW' WHERE listing_id=?", (listing_id,))
                db.execute("INSERT INTO provenance VALUES(?,?,?,?,?,?,?)", ("evt-" + uuid.uuid4().hex, listing_id, revision_id, "REVISION_CREATED", "owner", v["source"], now))
                for _, filename, mime, blob in files:
                    if not _valid_image(mime, blob):
                        raise InputError("Invalid image bytes")
                    digest = hashlib.sha256(blob).hexdigest()
                    db.execute("INSERT OR IGNORE INTO media VALUES(?,?,?,?,?,?,?)", ("med-" + uuid.uuid4().hex, revision_id, filename, mime, digest, blob, now))
                for old in prior_media:
                    db.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)", ("med-" + uuid.uuid4().hex, revision_id, old["filename"], old["mime"], old["sha256"], old["bytes"], now))
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("Could not create listing revision") from exc
        return {"listing_id": listing_id, "revision_id": revision_id, "revision_no": revision_no, "state": "PENDING_REVIEW", "file_count": len(files)}

    def review(self, listing_id: str, action: str) -> dict:
        if action not in {"approve", "reject"}:
            raise InputError("Review action must be approve or reject")
        state = "APPROVED" if action == "approve" else "REJECTED"
        with self.connection() as db:
            latest = self._latest(db, listing_id)
            if latest is None:
                raise KeyError("Listing not found")
            if latest["listing_status"] != "PENDING_REVIEW":
                raise InputError("Only pending listings can be reviewed")
            db.execute("UPDATE listings SET status=? WHERE listing_id=?", (state, listing_id))
            db.execute("UPDATE revisions SET status=? WHERE revision_id=?", (state, latest["revision_id"]))
            db.execute("INSERT INTO provenance VALUES(?,?,?,?,?,?,?)", ("evt-" + uuid.uuid4().hex, listing_id, latest["revision_id"], state, "owner", latest["source"], int(time.time())))
            return {"listing_id": listing_id, "revision_id": latest["revision_id"], "state": state}

    @staticmethod
    def _record(db: sqlite3.Connection, listing_id: str) -> dict | None:
        row = db.execute("""SELECT l.listing_id,l.property_id,l.transaction_type,l.status,
          r.revision_id,r.revision_no,r.title,r.description,r.price_vnd,r.area_m2,r.source,p.property_type,p.location
          FROM listings l JOIN revisions r USING(listing_id) JOIN properties p USING(property_id)
          WHERE l.listing_id=? ORDER BY r.revision_no DESC LIMIT 1""", (listing_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["media"] = [dict(m) for m in db.execute("SELECT media_id,filename,mime FROM media WHERE revision_id=? ORDER BY created_at,media_id", (row["revision_id"],))]
        return item

    def get_listing(self, listing_id: str, public: bool = True) -> dict | None:
        with self.connection() as db:
            item = self._record(db, listing_id)
            if item is not None and public and item["status"] != "APPROVED":
                return None
            if item is not None and public:
                return {
                    key: item[key] for key in ("listing_id", "transaction_type", "title", "description", "price_vnd", "area_m2", "property_type", "location")
                } | {"media": [{"media_id": media["media_id"], "mime": media["mime"]} for media in item["media"]]}
            return item

    def list_listings(self, query: dict[str, list[str]], public: bool = True) -> list[dict]:
        sql = "SELECT listing_id FROM listings WHERE status=?"
        args: list[object] = ["APPROVED" if public else "PENDING_REVIEW"]
        q = query.get("q", [""])[0].strip()[:160]
        if q:
            sql += " AND listing_id IN (SELECT l.listing_id FROM listings l JOIN revisions r USING(listing_id) JOIN properties p USING(property_id) WHERE l.status=? AND (r.title LIKE ? OR r.description LIKE ? OR p.location LIKE ?))"
            args.extend(["APPROVED" if public else "PENDING_REVIEW", *(f"%{q}%" for _ in range(3))])
        trans = query.get("transaction_type", [""])[0].upper()
        if trans in {"SALE", "RENT"}:
            sql += " AND transaction_type=?"
            args.append(trans)
        with self.connection() as db:
            ids = [row["listing_id"] for row in db.execute(sql + " ORDER BY created_at DESC", args).fetchall()]
        items = [self.get_listing(i, public=public) for i in ids]
        return [x for x in items if x]

    def list_all(self) -> list[dict]:
        with self.connection() as db:
            ids = [row["listing_id"] for row in db.execute("SELECT listing_id FROM listings ORDER BY created_at DESC")]
        return [item for item in (self.get_listing(i, public=False) for i in ids) if item]

    def media(self, media_id: str, public: bool = True) -> tuple[str, bytes] | None:
        with self.connection() as db:
            row = db.execute("""SELECT m.mime,m.bytes,l.status FROM media m JOIN revisions r USING(revision_id)
              JOIN listings l USING(listing_id) WHERE media_id=?""", (media_id,)).fetchone()
            if not row or (public and row["status"] != "APPROVED"):
                return None
            return row["mime"], row["bytes"]


class Application:
    def __init__(self, db_path: str | Path, owner_password: str | None = None, allowed_origins: set[str] | None = None, frontend: Path = FRONTEND, owner_password_file: str | Path | None = None, public_projection_path: str | Path | None = None, public_assets_root: str | Path | None = None, analytics_relay_url: str | None = None, test_mode: bool = False, legacy_owner_enabled: bool = True, test_namespace_projection_path: str | Path | None = None, test_namespace_assets_root: str | Path | None = None, test_namespace_token: str | None = None):
        self.legacy_owner_enabled = legacy_owner_enabled
        self.store = Store(db_path) if legacy_owner_enabled else None
        self.public_read_model = PublicProjectionReader(public_projection_path, public_assets_root)
        test_namespace_config = (test_namespace_projection_path, test_namespace_assets_root, test_namespace_token)
        if any(value is not None for value in test_namespace_config):
            if not all(value is not None for value in test_namespace_config):
                raise AuthError("Test namespace configuration is incomplete")
            if not isinstance(test_namespace_token, str) or len(test_namespace_token) < 32:
                raise AuthError("Test namespace token is invalid")
            self.test_namespace_reader = PublicProjectionReader(test_namespace_projection_path, test_namespace_assets_root)
            self.test_namespace_token = test_namespace_token
        else:
            self.test_namespace_reader = None
            self.test_namespace_token = None
        self.analytics_relay = AnalyticsRelay(analytics_relay_url, test_mode=test_mode)
        if not legacy_owner_enabled and (owner_password is not None or owner_password_file is not None):
            raise AuthError("Public Customer Web cannot configure a legacy Owner credential")
        if owner_password is not None and owner_password_file is not None:
            raise AuthError("Configure exactly one Owner password source")
        if not legacy_owner_enabled:
            self.owner_configured = False
        elif owner_password_file is not None:
            existing_password = read_owner_password_file(owner_password_file)
            self.owner_configured = self.store.provision_owner(existing_password, allow_existing_short=True, refresh=True)
        else:
            self.owner_configured = self.store.provision_owner(owner_password)
        self.allowed_origins = allowed_origins or {"https://haisala.io.vn"}
        self.frontend = frontend
        self.login_attempts: dict[str, list[float]] = {}
        self.login_lock = threading.Lock()

    @staticmethod
    def _cookie(headers: dict[str, str]) -> str | None:
        jar = SimpleCookie()
        try:
            jar.load(headers.get("cookie", ""))
        except Exception:
            return None
        return jar[COOKIE_NAME].value if COOKIE_NAME in jar else None

    def _response(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", extra: dict[str, str] | None = None) -> Response:
        headers = {
            "Content-Type": content_type,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
        }
        if content_type.startswith("application/json") or content_type.startswith("image/"):
            headers["Cache-Control"] = "no-store"
        if extra:
            headers.update(extra)
        return Response(status, body, headers)

    def _json_response(self, status: int, data: object, extra: dict[str, str] | None = None) -> Response:
        return self._response(status, _json(data), extra=extra)

    def _owner(self, headers: dict[str, str]) -> bool:
        return bool(self.legacy_owner_enabled and self.store and self.store.valid_session(self._cookie(headers)))

    def _test_namespace(self, method: str, path: str, query: dict[str, list[str]], headers: dict[str, str]) -> Response:
        """Read an isolated, authenticated projection namespace; never mutates public state."""
        if self.test_namespace_reader is None or self.test_namespace_token is None:
            return self._json_response(404, {"error": "Not found"})
        if method != "GET":
            return self._json_response(404, {"error": "Not found"})
        supplied = headers.get("authorization", "")
        scheme, _, credential = supplied.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, self.test_namespace_token):
            return self._json_response(401, {"error": "Test namespace authentication required"}, {"WWW-Authenticate": "Bearer"})
        subpath = path[len(TEST_NAMESPACE_PREFIX):] or "/"
        if subpath == "/health":
            return self._json_response(200, {"ok": True, "storage": "isolated_test_projection"})
        try:
            if subpath == "/api/listings":
                return self._json_response(200, {"items": self.test_namespace_reader.list_listings(query)})
            match = re.fullmatch(r"/api/listings/([A-Za-z0-9-]+)", subpath)
            if match:
                item = self.test_namespace_reader.get_listing(match.group(1))
                return self._json_response(200, item) if item else self._json_response(404, {"error": "Listing not found"})
            match = re.fullmatch(r"/media/([A-Za-z0-9-]+)", subpath)
            if match:
                item = self.test_namespace_reader.media(match.group(1))
                return self._response(200, item[1], item[0]) if item else self._json_response(404, {"error": "Media not found"})
            return self._json_response(404, {"error": "Not found"})
        except ProjectionUnavailable:
            return self._json_response(503, {"error": "Test projection unavailable"})

    def handle(self, method: str, target: str, headers: dict[str, str] | None = None, body: bytes = b"", client_ip: str = "unknown") -> Response:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            parts = urlsplit(target)
            path = unquote(parts.path)
            query = parse_qs(parts.query, max_num_fields=40)
        except ValueError:
            return self._json_response(400, {"error": "Malformed request path"})
        if len(body) > MAX_REQUEST:
            return self._json_response(413, {"error": "Request too large"})
        if path == TEST_NAMESPACE_PREFIX or path.startswith(TEST_NAMESPACE_PREFIX + "/"):
            return self._test_namespace(method, path, query, headers)
        if method == "GET" and path == "/health":
            return self._json_response(200, {"ok": True, "storage": "admin_projection" if not self.legacy_owner_enabled else "sqlite"})
        if method == "GET" and path in ASSET_MAP:
            name, mime = ASSET_MAP[path]
            try:
                data = (self.frontend / name).read_bytes()
            except OSError:
                return self._json_response(503, {"error": "Frontend asset missing"})
            return self._response(200, data, mime, {"Cache-Control": "no-cache"})
        direct_listing = re.fullmatch(r"/tin/([A-Za-z0-9-]{1,128})", path)
        if method == "GET" and direct_listing:
            try:
                # Resolve from the current approved projection on every direct
                # open; stale, revoked and unknown links fail closed.
                if self.public_read_model.get_listing(direct_listing.group(1)) is None:
                    return self._json_response(404, {"error": "Listing not found"})
                try:
                    page = (self.frontend / "index.html").read_bytes()
                except OSError:
                    return self._json_response(503, {"error": "Frontend asset missing"})
                return self._response(200, page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            except ProjectionUnavailable as exc:
                return self._json_response(503, {"error": str(exc)})
        if method == "GET" and path == "/api/listings":
            try:
                return self._json_response(200, {"items": self.public_read_model.list_listings(query)})
            except ProjectionUnavailable as exc:
                return self._json_response(503, {"error": str(exc)})
        if method == "GET" and path.startswith("/api/listings/"):
            try:
                item = self.public_read_model.get_listing(path.rsplit("/", 1)[-1])
                return self._json_response(200, item) if item else self._json_response(404, {"error": "Listing not found"})
            except ProjectionUnavailable as exc:
                return self._json_response(503, {"error": str(exc)})
        if method == "GET" and path.startswith("/media/"):
            try:
                item = self.public_read_model.media(path.rsplit("/", 1)[-1])
                return self._response(200, item[1], item[0]) if item else self._json_response(404, {"error": "Media not found"})
            except ProjectionUnavailable as exc:
                return self._json_response(503, {"error": str(exc)})
        if method == "GET" and path == "/api/admin/pending":
            if not self.legacy_owner_enabled:
                return self._json_response(404, {"error": "Not found"})
            if not self._owner(headers):
                return self._json_response(401, {"error": "Owner authentication required"})
            return self._json_response(200, {"items": self.store.list_listings({}, public=False)})
        if method == "GET" and path == "/api/admin/listings":
            if not self.legacy_owner_enabled:
                return self._json_response(404, {"error": "Not found"})
            if not self._owner(headers):
                return self._json_response(401, {"error": "Owner authentication required"})
            return self._json_response(200, {"items": self.store.list_all()})
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return self._json_response(404, {"error": "Not found"})
        if headers.get("origin") not in self.allowed_origins:
            return self._json_response(403, {"error": "Origin rejected"})
        if method == "POST" and path == "/api/analytics/events":
            if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self._json_response(415, {"error": "Analytics JSON required"})
            if not body or len(body) > 4096:
                return self._json_response(413 if body else 400, {"error": "Analytics request size invalid"})
            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return self._json_response(400, {"error": "Analytics JSON invalid"})
            try:
                receipt = self.analytics_relay.send(payload, headers["origin"])
                return self._json_response(200 if receipt["duplicate"] else 202, receipt)
            except AnalyticsRelayError as exc:
                status = exc.status if 400 <= exc.status < 500 else 503
                return self._json_response(status, {"error": "Analytics rejected" if status < 500 else "Analytics unavailable"})
        if method == "POST" and path == "/api/login":
            if not self.legacy_owner_enabled:
                return self._json_response(404, {"error": "Not found"})
            now = time.monotonic()
            with self.login_lock:
                attempts = [at for at in self.login_attempts.get(client_ip, []) if now - at < 900]
                if len(attempts) >= 5:
                    self.login_attempts[client_ip] = attempts
                    return self._json_response(429, {"error": "Too many login attempts; retry later"})
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("Expected JSON object")
                token = self.store.authenticate(data.get("password", ""))
            except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
                with self.login_lock:
                    self.login_attempts[client_ip] = attempts + [now]
                return self._json_response(401, {"error": "Authentication failed"})
            with self.login_lock:
                self.login_attempts.pop(client_ip, None)
            cookie = f"{COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=28800"
            return self._json_response(200, {"ok": True}, {"Set-Cookie": cookie})
        if not self._owner(headers):
            if not self.legacy_owner_enabled:
                return self._json_response(404, {"error": "Not found"})
            return self._json_response(401, {"error": "Owner authentication required"})
        if method == "POST" and path == "/api/logout":
            self.store.revoke(self._cookie(headers))
            return self._json_response(200, {"ok": True}, {"Set-Cookie": f"{COOKIE_NAME}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"})
        if method == "POST" and path == "/api/intakes":
            try:
                fields, files = parse_multipart(headers.get("content-type", ""), body)
                result = self.store.create_intake(fields, files)
                return self._json_response(201, result)
            except DuplicateError as exc:
                return self._json_response(409, {"error": str(exc)})
            except InputError as exc:
                return self._json_response(400, {"error": str(exc)})
        match = re.fullmatch(r"/api/listings/([A-Za-z0-9-]+)/review", path)
        if method == "POST" and match:
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("Expected JSON object")
                return self._json_response(200, self.store.review(match.group(1), data.get("action", "")))
            except KeyError:
                return self._json_response(404, {"error": "Listing not found"})
            except (InputError, TypeError, json.JSONDecodeError, ValueError, AttributeError) as exc:
                return self._json_response(400, {"error": str(exc)})
        match = re.fullmatch(r"/api/listings/([A-Za-z0-9-]+)/revisions", path)
        if method == "POST" and match:
            try:
                fields, files = parse_multipart(headers.get("content-type", ""), body)
                return self._json_response(201, self.store.create_revision(match.group(1), fields, files))
            except KeyError:
                return self._json_response(404, {"error": "Listing not found"})
            except DuplicateError as exc:
                return self._json_response(409, {"error": str(exc)})
            except InputError as exc:
                return self._json_response(400, {"error": str(exc)})
        return self._json_response(404, {"error": "Not found"})


def serve() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    host = os.environ.get("SALA_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("SALA_PORT", "18877"))
    if host not in {"127.0.0.1", "::1", "localhost"} or port in {8765, 18876}:
        raise SystemExit("Refusing non-loopback or reserved bind target")
    owner_password = os.environ.get("SALA_OWNER_PASSWORD")
    owner_password_file = os.environ.get("SALA_OWNER_PASSWORD_FILE")
    if owner_password and owner_password_file:
        raise SystemExit("Configure exactly one Owner password source")
    test_namespace_projection = None
    test_namespace_assets = None
    test_namespace_token = None
    if os.environ.get("SALA_TEST_NAMESPACE_ENABLED") == "1":
        root_value = os.environ.get("SALA_TEST_NAMESPACE_ROOT", "").strip()
        if not root_value:
            raise SystemExit("Test namespace root is not configured")
        root = Path(root_value).expanduser()
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise SystemExit("Test namespace root must be a private service-owned directory")
        token_path = root / "control" / "bearer-token"
        if token_path.is_symlink() or not token_path.is_file():
            raise SystemExit("Test namespace token is unavailable")
        token_info = token_path.stat()
        if (token_info.st_uid != os.getuid() or stat.S_IMODE(token_info.st_mode) != 0o600
                or not stat.S_ISREG(token_info.st_mode)):
            raise SystemExit("Test namespace token file must be private and service-owned")
        test_namespace_token = token_path.read_text(encoding="ascii").strip()
        if len(test_namespace_token) < 32:
            raise SystemExit("Test namespace token is invalid")
        projection_relative = Path(os.environ.get("SALA_TEST_NAMESPACE_PROJECTION_RELATIVE", "projection/listings.json"))
        if (projection_relative.is_absolute() or any(part in {"", ".", ".."} for part in projection_relative.parts)
                or projection_relative.as_posix() not in {"projection/listings.json", "admin-test-root/public-preview/data/listings.json"}):
            raise SystemExit("Test namespace projection path is invalid")
        test_namespace_projection = root / projection_relative
        assets_relative = os.environ.get("SALA_TEST_NAMESPACE_ASSETS_RELATIVE", ".").strip()
        if assets_relative not in {".", "admin-test-root/public-assets"}:
            raise SystemExit("Test namespace asset path is invalid")
        test_namespace_assets = root if assets_relative == "." else root / assets_relative
        projection_dirs = [root.joinpath(*projection_relative.parts[:index]) for index in range(1, len(projection_relative.parts))]
        asset_required = root / "assets" if assets_relative == "." else root / "admin-test-root" / "public-assets"
        for directory in (*projection_dirs, asset_required, token_path.parent):
            if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise SystemExit("Test namespace storage directories must be private and service-owned")
    app = Application(
        os.environ.get("SALA_DB_PATH", str(ROOT / "var" / "customer_web.sqlite3")), owner_password,
        set(filter(None, os.environ.get("SALA_ALLOWED_ORIGINS", "https://haisala.io.vn").split(","))),
        owner_password_file=owner_password_file,
        public_projection_path=os.environ.get("SALA_PUBLIC_PROJECTION_PATH") or None,
        public_assets_root=os.environ.get("SALA_PUBLIC_ASSETS_ROOT") or None,
        analytics_relay_url=os.environ.get("SALA_ADMIN_ANALYTICS_URL") or None,
        test_mode=os.environ.get("SALA_ENV") == "test",
        legacy_owner_enabled=False,
        test_namespace_projection_path=test_namespace_projection,
        test_namespace_assets_root=test_namespace_assets,
        test_namespace_token=test_namespace_token,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            response = app.handle("GET", self.path, dict(self.headers), client_ip=self.client_address[0])
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            response = app.handle("POST", self.path, dict(self.headers), body, self.client_address[0])
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, fmt, *args):
            # Do not log paths, form data, cookies, or request headers.
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
