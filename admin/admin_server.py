#!/usr/bin/env python3
"""Local-only BMT Property Hub intake/admin service.

This service intentionally keeps intake media and drafts outside the public
website root. It never writes Property Core in this V1; public promotion is a
separate, explicit boundary for a later owner-approved step.
"""
from __future__ import annotations

import html
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import threading
import tempfile
from collections import deque
import uuid
import fcntl
import time
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from property_memory.intake_review import process_intake
from property_memory.providers.openai_provider import OpenAIPropertyProvider
from property_memory.ai_gateway import AIGateway
from property_memory.review_center import ReviewCenter
from property_memory.projection_builder import ProjectionError, _is_public_asset_ref, _media_rights_allowed, _slug, build_public_projection
from property_memory.staging_artifact_writer import StagingArtifactError, withdraw_staging_web_record, write_staging_web_artifact
from property_memory.ai9_layers import OWNER_CONFIRMED_TEST_COMMENT, OWNER_CONFIRMED_TEST_REVIEW, run_ai9_preview
from property_memory.content_studio import build_copy_studio_preview, build_generative_internal_draft, build_generative_content_batch, GENERATIVE_MODE
from property_memory.evidence import validate_evidence_payload
from property_memory.runtime_config import diagnostic_payload, health_payload, load_runtime_config
from property_memory.runtime_lock import validate_runtime_lock
from property_memory.store import PropertyStore
from property_memory.usage_ledger import UsageLedger
from property_memory.analytics_store import AnalyticsStore, AnalyticsValidationError, EVENT_TYPES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = Path(os.environ.get("BMT_ADMIN_PRIVATE_ROOT", "/Users/trandinhhieu/BMTPropertyHubPrivate"))
INBOX_ROOT = PRIVATE_ROOT / "inbox"
INDEX_PATH = PRIVATE_ROOT / "index.json"
AUDIT_LOG_PATH = PRIVATE_ROOT / "audit.jsonl"
ERROR_QUEUE_PATH = PRIVATE_ROOT / "error-queue.json"
PUBLIC_DATA = PROJECT_ROOT / "data" / "listings.json"
HOST = os.environ.get("BMT_ADMIN_HOST", "127.0.0.1")
PORT = int(os.environ.get("BMT_ADMIN_PORT", "8876"))
PASSWORD_FILE = Path(os.environ.get("BMT_ADMIN_PASSWORD_FILE", "/Users/trandinhhieu/BMTPropertyHubPrivate/admin-password"))
ALLOWED_REMOTE_IP = os.environ.get("BMT_ADMIN_ALLOWED_IP", "")
SESSION_TTL = 8 * 60 * 60
SECURE_COOKIE = os.environ.get("BMT_ADMIN_SECURE_COOKIE", "0").strip().lower() in {"1", "true", "yes"}
RETRY_LIMIT = 3
MAX_BODY = 50 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".txt", ".json", ".md"}
CHANNELS = ("public_web", "zalo_company", "facebook", "tiktok")
SESSIONS: dict[str, float] = {}
SESSION_OWNERS: dict[str, str] = {}
PRIVATE_FILE_LOCK = threading.RLock()
PROPERTY_OPERATION_LOCK = threading.RLock()
REVIEW_CENTER: ReviewCenter | None = None
_ANALYTICS_STORE: AnalyticsStore | None = None
_ANALYTICS_RATE_LOCK = threading.Lock()
_ANALYTICS_RATE_TIMES: deque[float] = deque()
_AI_ENABLED = os.environ.get("BMT_OPENAI_EXTRACTION", "0").strip().lower() in {"1", "true", "yes"}
_CONTENT_LIVE_ENABLED = os.environ.get("BMT_OPENAI_CONTENT_LIVE", "0").strip().lower() in {"1", "true", "yes"}
class LazyUsageLedger:
    """Open the private usage database only when a usage operation is requested."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._instance: UsageLedger | None = None
        self._lock = threading.Lock()

    def _get(self) -> UsageLedger:
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = UsageLedger(self.path)
        return self._instance

    def record(self, **kwargs: Any) -> dict[str, Any]:
        return self._get().record(**kwargs)

    def report(self, **kwargs: Any) -> dict[str, Any]:
        return self._get().report(**kwargs)


_USAGE_LEDGER = LazyUsageLedger(PRIVATE_ROOT / "provider-usage.sqlite3")
_AI_PROVIDER = OpenAIPropertyProvider(usage_sink=_USAGE_LEDGER)
AI_GATEWAY = AIGateway(provider=_AI_PROVIDER, enabled=_AI_ENABLED, allowed_models={_AI_PROVIDER.model}, model=_AI_PROVIDER.model, max_requests=int(os.environ.get("BMT_OPENAI_MAX_REQUESTS", "1")))

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_admin_content_batch(package: dict[str, Any], ai_run_id: str, *, provider: Any | None = None) -> dict[str, Any]:
    """Admin boundary: explicit Content action only; live provider is disabled by default."""
    candidate = package.get("candidate") or {}
    run = next((item for item in package.get("ai9_runs", []) if item.get("ai_run_id") == ai_run_id), None)
    if run is None:
        return {"status": "BLOCKED", "reason_codes": ["AI_RUN_NOT_FOUND"], "published": False, "variants": []}
    provenance = {"intake_id": candidate.get("intake_id"), "review_id": package.get("review_id"),
                  "citations": (run.get("layers", {}).get("provenance") or {}).get("citations", [])}
    return build_generative_content_batch(
        generation_id=f"content-{ai_run_id}", facts=candidate.get("facts") or {}, provenance=provenance,
        rights_status=candidate.get("rights_status"), media=candidate.get("media") or [], provider=provider,
        model=_AI_PROVIDER.model, usage_sink=_USAGE_LEDGER)


class AuthDiagnostic:
    """Opt-in, one-shot, private auth rejection diagnostics."""

    def __init__(self) -> None:
        self.activation_file = Path(os.environ.get("BMT_AUTH_DIAGNOSTIC_ACTIVATION_FILE", ""))
        self.marker_file = Path(os.environ.get("BMT_AUTH_DIAGNOSTIC_MARKER_FILE", ""))
        self.ack_path = PRIVATE_ROOT / "auth-diagnostic-ack.json"
        self.record_path = PRIVATE_ROOT / "auth-diagnostic.json"
        self.lock = threading.Lock()
        self._acknowledge_marker()

    def _acknowledge_marker(self) -> None:
        try:
            marker = json.loads(self.marker_file.read_text(encoding="utf-8"))
            marker_id = str(marker.get("marker_id", ""))
            expires_at = float(marker.get("expires_at", 0))
            if not marker_id or expires_at <= datetime.now(timezone.utc).timestamp():
                return
            ack = {
                "pid": os.getpid(),
                "marker_id_sha256": hashlib.sha256(marker_id.encode("utf-8")).hexdigest(),
                "acknowledged_at": now_iso(),
                "expires_at": expires_at,
                "status": "attached",
            }
            self.ack_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.ack_path.parent.chmod(0o700)
            tmp = self.ack_path.with_name(self.ack_path.name + ".tmp")
            tmp.write_text(json.dumps(ack, sort_keys=True) + "\n", encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.ack_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    def _consume(self) -> bool:
        if not self.activation_file or not self.activation_file.is_file():
            return False
        try:
            with self.activation_file.open("r+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                activation = json.loads(handle.read())
                expires_at = float(activation.get("expires_at", 0))
                if activation.get("enabled") is not True or expires_at <= datetime.now(timezone.utc).timestamp():
                    return False
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps({"enabled": False, "consumed": True}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def record_once(self, reason: str) -> None:
        with self.lock:
            if reason not in {"ALLOWLIST_DENIED", "PASSWORD_REJECTED"} or not self._consume():
                return
            record = {"reason": reason, "at": now_iso()}
            try:
                self.record_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                tmp = self.record_path.with_name(self.record_path.name + ".tmp")
                tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
                tmp.chmod(0o600)
                tmp.replace(self.record_path)
            except OSError:
                return


class AuthCorrelationSink:
    """Short-lived, application-layer auth correlation with secret-free fields."""

    RETENTION_SECONDS = 10 * 60
    ALLOWED_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})

    def __init__(self) -> None:
        self.path = PRIVATE_ROOT / "auth-correlation.jsonl"
        self.lock = threading.Lock()

    @staticmethod
    def _status_class(status: int | str) -> str:
        try:
            code = int(status)
        except (TypeError, ValueError):
            return "unknown"
        return f"{code // 100}xx" if 100 <= code <= 599 else "unknown"

    def record(self, stage: str, path: str, status: int | str, branch: str = "") -> str:
        normalized = urlparse(path).path
        if normalized not in self.ALLOWED_PATHS:
            return ""
        correlation = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:24]
        now = time.time()
        row = {
            "timestamp": now_iso(),
            "correlation_hash": correlation,
            "route_identity": "staging-auth",
            "method": "POST" if normalized == "/api/auth/login" else "POST",
            "path": normalized,
            "status_class": self._status_class(status),
            "stage": stage,
            "auth_branch": branch or "none",
            "pid": os.getpid(),
            "process_identity": "bmt-property-hub-staging",
        }
        with self.lock:
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.path.parent.chmod(0o700)
                lines = []
                if self.path.is_file():
                    if now - self.path.stat().st_mtime < self.RETENTION_SECONDS:
                        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line]
                lines.append(json.dumps(row, sort_keys=True))
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.chmod(0o600)
                tmp.replace(self.path)
            except OSError:
                return correlation
        return correlation


AUTH_DIAGNOSTIC = AuthDiagnostic()
AUTH_CORRELATION = AuthCorrelationSink()


def route_classification(path: str, method: str = "GET") -> str:
    """Return the explicit privacy class used by the Admin boundary."""
    normalized = urlparse(path).path
    if normalized in {"/api/health", "/api/diagnostic"}:
        return "SAFE_HEALTH_DIAGNOSTIC"
    if normalized in {"/login", "/logout"}:
        return "PUBLIC"
    if normalized == "/api/analytics/events" and method.upper() in {"POST", "OPTIONS"}:
        return "PUBLIC_MINIMAL_EVENT_INGESTION"
    if normalized == "/" or normalized.startswith("/api/"):
        return "PRIVATE_AUTH_REQUIRED"
    return "PUBLIC"


def _is_loopback_host(host: str) -> bool:
    value = (host or "").strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _origin_from_url(value: str, *, allow_path: bool) -> str | None:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.query or parsed.fragment or (not allow_path and parsed.path not in {"", "/"}):
            return None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        netloc = host if port is None or default_port else f"{host}:{port}"
        return f"{parsed.scheme}://{netloc}"
    except (TypeError, ValueError):
        return None


def _local_loopback_development() -> bool:
    environment = os.environ.get("BMT_ENV", "dev").strip().lower()
    explicitly_enabled = os.environ.get("BMT_ADMIN_LOCAL_HTTP_DEV", "").strip().lower() in {"1", "true", "yes"}
    return explicitly_enabled and environment in {"dev", "test"} and _is_loopback_host(HOST) and not ALLOWED_REMOTE_IP.strip()


def security_config_error() -> str | None:
    """Require explicit HTTPS cookie/origin configuration outside loopback dev."""
    configured = os.environ.get("BMT_ADMIN_PUBLIC_ORIGIN", "").strip()
    local_development = _local_loopback_development()
    if local_development:
        if not configured:
            return None
        origin = _origin_from_url(configured, allow_path=False)
        parsed = urlparse(origin) if origin else None
        if parsed and parsed.scheme == "http" and _is_loopback_host(parsed.hostname or ""):
            return None
    if not SECURE_COOKIE:
        return "SECURE_COOKIE_REQUIRED"
    origin = _origin_from_url(configured, allow_path=False) if configured else None
    if origin is None:
        return "PUBLIC_ORIGIN_REQUIRED"
    if not origin.startswith("https://"):
        return "HTTPS_ORIGIN_REQUIRED"
    if not ALLOWED_REMOTE_IP.strip():
        return "REMOTE_IP_ALLOWLIST_REQUIRED"
    return None


def trusted_origins() -> set[str]:
    configured = os.environ.get("BMT_ADMIN_PUBLIC_ORIGIN", "").strip()
    origin = _origin_from_url(configured, allow_path=False) if configured else None
    if origin:
        return {origin}
    if not _local_loopback_development():
        return set()
    return {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", f"http://[::1]:{PORT}"}


def analytics_allowed_origins() -> set[str]:
    configured = os.environ.get("BMT_ANALYTICS_ALLOWED_ORIGINS", "")
    allowed = {_origin_from_url(item.strip(), allow_path=False) for item in configured.split(",") if item.strip()}
    allowed.discard(None)
    if _local_loopback_development():
        allowed.update(trusted_origins())
    return allowed


def get_analytics_store() -> AnalyticsStore:
    global _ANALYTICS_STORE
    if _ANALYTICS_STORE is None:
        try:
            retention_days = int(os.environ.get("BMT_ANALYTICS_RETENTION_DAYS", "90"))
        except ValueError as exc:
            raise ValueError("ANALYTICS_RETENTION_CONFIG_INVALID") from exc
        _ANALYTICS_STORE = AnalyticsStore(PRIVATE_ROOT / "analytics.sqlite3", retention_days=retention_days)
    return _ANALYTICS_STORE


def _analytics_property_is_public(property_id: str) -> bool:
    center = REVIEW_CENTER
    if center is None:
        return False
    try:
        center._load_packages()
        package, decision = _latest_public_release(center, property_id)
        if not package or not decision or decision.get("action") != "APPROVE_PUBLICATION":
            return False
        review_id = str(package.get("review_id") or "")
        snapshot = _public_release_snapshot(center, review_id, cleanup_stale_preview=False)
        return snapshot.get("state") == "PUBLIC_ALLOWED" and snapshot.get("property_id") == property_id
    except (OSError, KeyError, TypeError, ValueError, StagingArtifactError, json.JSONDecodeError):
        return False


def _analytics_rate_allowed() -> bool:
    try:
        limit = int(os.environ.get("BMT_ANALYTICS_MAX_PER_MINUTE", "120"))
    except ValueError:
        return False
    if not 1 <= limit <= 600:
        return False
    now = time.monotonic()
    with _ANALYTICS_RATE_LOCK:
        while _ANALYTICS_RATE_TIMES and now - _ANALYTICS_RATE_TIMES[0] >= 60:
            _ANALYTICS_RATE_TIMES.popleft()
        if len(_ANALYTICS_RATE_TIMES) >= limit:
            return False
        _ANALYTICS_RATE_TIMES.append(now)
    return True


def configure_review_center(center: ReviewCenter) -> None:
    """Inject the private domain workflow; tests/runtime may configure this lazily."""
    global REVIEW_CENTER
    REVIEW_CENTER = center


def _public_preview_paths() -> tuple[Path, Path]:
    configured = os.environ.get("BMT_ADMIN_PREVIEW_ROOT", "").strip()
    if not configured:
        raise ValueError("PREVIEW_TARGET_NOT_CONFIGURED")
    root = Path(configured).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        root.relative_to(temp_root)
    except ValueError as exc:
        raise ValueError("PREVIEW_TARGET_UNSAFE") from exc
    if root == temp_root or root == PROJECT_ROOT or PROJECT_ROOT in root.parents:
        raise ValueError("PREVIEW_TARGET_UNSAFE")
    return root / "canonical" / "projections.json", root / "data" / "listings.json"


def _withdraw_private_preview(property_id: str) -> int:
    try:
        canonical_path, website_path = _public_preview_paths()
    except ValueError as exc:
        if str(exc) == "PREVIEW_TARGET_NOT_CONFIGURED":
            return 0
        raise
    removed = 0
    for path in (canonical_path, website_path):
        removed += withdraw_staging_web_record(path, property_id)["removed"]
    return removed


def _latest_public_release(center: ReviewCenter, property_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    decisions: list[tuple[str, str, int, dict[str, Any], dict[str, Any]]] = []
    for review_id, package in center.packages.items():
        for index, decision in enumerate(package.get("public_release_decisions", [])):
            if decision.get("property_id") == property_id:
                decisions.append((str(decision.get("at") or ""), review_id, index, package, decision))
    if not decisions:
        return None, None
    latest_time = max(item[0] for item in decisions)
    latest = [item for item in decisions if item[0] == latest_time]
    if len({item[1] for item in latest}) > 1:
        raise ValueError("PUBLIC_RELEASE_ORDER_AMBIGUOUS")
    _, _, _, package, decision = max(latest, key=lambda item: item[2])
    return package, decision


def _public_release_snapshot(center: ReviewCenter, review_id: str, *, cleanup_stale_preview: bool = False) -> dict[str, Any]:
    center._load_packages()
    package = center.packages.get(review_id)
    if package is None:
        raise KeyError("REVIEW_NOT_FOUND")
    property_id = str((package.get("mutation_result") or {}).get("property_id") or "")
    blockers: list[str] = []
    media_summary = {"active": 0, "eligible": 0, "blocked": 0}
    canonical_facts: dict[str, Any] = {}
    gate_a = package.get("decision") or {}
    if (package.get("state") != "APPROVED" or gate_a.get("type") != "APPROVE"
            or int(gate_a.get("revision", -1)) != int(package.get("review_revision", -2)) or not property_id):
        blockers.append("GATE_A_APPROVAL_REQUIRED")
    prop = center.store.get(property_id) if property_id else None
    if prop is None:
        blockers.append("PROPERTY_NOT_FOUND")
        property_revision = 0
    else:
        property_revision = int(prop.get("property_revision") or 0)
        if property_revision < 1:
            blockers.append("PROPERTY_REVISION_REQUIRED")
        required = ("title", "property_type", "address", "price")
        if any(prop.get(field) in (None, "", []) for field in required):
            blockers.append("PUBLIC_FACTS_INCOMPLETE")
        price = prop.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            blockers.append("PUBLIC_PRICE_INVALID")
        attributes = prop.get("attributes") if isinstance(prop.get("attributes"), dict) else {}
        price_type = str(attributes.get("price_type") or "").upper()
        if price_type not in {"SALE", "RENT"}:
            blockers.append("TRANSACTION_TYPE_UNCONFIRMED")
        if price_type == "RENT" and str(attributes.get("price_period") or "MONTH").upper() != "MONTH":
            blockers.append("RENT_PERIOD_UNSUPPORTED")
        public_status = attributes.get("public_status") or ("Đang cho thuê" if price_type == "RENT" else "Đang bán")
        allowed_status = {"Đang cho thuê", "Đã cho thuê"} if price_type == "RENT" else {"Đang bán", "Đã cọc", "Đã bán"}
        if public_status not in allowed_status:
            blockers.append("PUBLIC_STATUS_UNSUPPORTED")
        if not center.store.list_source_appearances(property_id):
            blockers.append("PROVENANCE_APPEARANCE_REQUIRED")
        if any(conflict.get("status") == "OPEN" for conflict in center.store.list_conflicts(property_id)):
            blockers.append("OPEN_PROPERTY_CONFLICTS")
        active_media = [item for item in center.store.list_media_history(property_id) if item.get("active")]
        media_summary["active"] = len(active_media)
        public_slug = attributes.get("public_slug") or _slug(str(prop.get("title") or property_id))
        for item in active_media:
            if not _media_rights_allowed(center.store, property_id, item.get("appearance_id")):
                blockers.append("PUBLIC_MEDIA_RIGHTS_DENIED")
                media_summary["blocked"] += 1
                continue
            if not _is_public_asset_ref(str(item.get("media_ref") or ""), str(public_slug)):
                blockers.append("PUBLIC_MEDIA_REFERENCE_UNSAFE")
                media_summary["blocked"] += 1
                continue
            media_summary["eligible"] += 1
        canonical_facts = {
            "title": prop.get("title"), "property_type": prop.get("property_type"),
            "address": prop.get("address"), "area": prop.get("area"),
            "price": prop.get("price"), "price_type": price_type,
            "public_status": public_status, "legal_status": prop.get("legal_status"),
        }
        try:
            _public_preview_paths()
        except ValueError as exc:
            blockers.append(str(exc))

    release_package, latest_release = _latest_public_release(center, property_id) if property_id else (None, None)
    belongs_to_review = bool(release_package and release_package.get("review_id") == review_id)
    if latest_release and release_package and release_package.get("review_id") != review_id:
        if latest_release.get("action") == "APPROVE_PUBLICATION" and int(latest_release.get("property_revision") or 0) == property_revision:
            blockers.append("PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW")
    if not latest_release:
        state = "NOT_APPROVED"
    elif latest_release.get("action") == "REVOKE_PUBLICATION":
        state = "REVOKED"
    elif int(latest_release.get("property_revision") or 0) != property_revision:
        state = "STALE"
    else:
        state = "PUBLIC_ALLOWED" if belongs_to_review else "PUBLIC_ALLOWED_OTHER_REVIEW"
    active = bool(latest_release and latest_release.get("action") == "APPROVE_PUBLICATION")
    if active and blockers and "PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW" not in blockers:
        state = "INELIGIBLE"

    preview_removed = 0
    should_withdraw = bool(active and (state in {"STALE", "INELIGIBLE", "PUBLIC_ALLOWED_OTHER_REVIEW"})) or state == "REVOKED"
    if cleanup_stale_preview and property_id and should_withdraw:
        try:
            preview_removed = _withdraw_private_preview(property_id)
        except (OSError, ValueError, StagingArtifactError, json.JSONDecodeError):
            blockers.append("PREVIEW_WITHDRAWAL_FAILED")
            state = "INELIGIBLE"

    own_active = bool(belongs_to_review and latest_release and latest_release.get("action") == "APPROVE_PUBLICATION")
    can_revoke = own_active and state in {"PUBLIC_ALLOWED", "INELIGIBLE", "STALE"}
    can_approve = not blockers and not (active and state == "PUBLIC_ALLOWED") and not (active and state == "PUBLIC_ALLOWED_OTHER_REVIEW")
    return {
        "review_id": review_id, "review_revision": int(package.get("review_revision") or 0),
        "gate_a_state": package.get("state"), "property_id": property_id or None,
        "property_revision": property_revision, "state": state,
        "canonical_facts": canonical_facts, "media_summary": media_summary,
        "release_action": latest_release.get("action") if belongs_to_review and latest_release else None,
        "release_owner_id": latest_release.get("owner_id") if belongs_to_review and latest_release else None,
        "release_review_revision": latest_release.get("review_revision") if belongs_to_review and latest_release else None,
        "blockers": list(dict.fromkeys(blockers)), "can_approve": can_approve,
        "can_revoke": can_revoke, "preview_removed": preview_removed,
    }


def _restore_preview_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".bak").unlink(missing_ok=True)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(previous); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def safe_name(name: str) -> str:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-") or "upload"
    return base[:120]


def parse_multipart_intake(raw: bytes, content_type: str) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """Decode browser multipart fields as UTF-8 without replacement characters."""
    msg = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {content_type}\r\n\r\n").encode("ascii") + raw
    )
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append((safe_name(filename), payload))
        elif name:
            charset = part.get_content_charset() or "utf-8"
            try:
                fields[name] = payload.decode(charset, errors="strict")
            except (LookupError, UnicodeDecodeError) as exc:
                raise ValueError("INVALID_UTF8_FORM_FIELD") from exc
    return fields, files


def ensure_private() -> None:
    PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    INBOX_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        INDEX_PATH.write_text("[]\n", encoding="utf-8")
        INDEX_PATH.chmod(0o600)
    if not ERROR_QUEUE_PATH.exists():
        ERROR_QUEUE_PATH.write_text("[]\n", encoding="utf-8")
        ERROR_QUEUE_PATH.chmod(0o600)
    if not AUDIT_LOG_PATH.exists():
        AUDIT_LOG_PATH.touch(mode=0o600)
    AUDIT_LOG_PATH.chmod(0o600)


def load_index() -> list[dict]:
    ensure_private()
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_index(items: list[dict]) -> None:
    with PRIVATE_FILE_LOCK:
        # Merge by immutable intake ID under the same lock so concurrent
        # handlers cannot overwrite records created from another snapshot.
        try:
            current = json.loads(INDEX_PATH.read_text(encoding="utf-8")) if INDEX_PATH.exists() else []
        except (OSError, json.JSONDecodeError):
            current = []
        merged = {item.get("id"): item for item in current if isinstance(item, dict) and item.get("id")}
        order = [item.get("id") for item in current if isinstance(item, dict) and item.get("id")]
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item["id"] not in merged:
                order.insert(0, item["id"])
            merged[item["id"]] = item
        items = [merged[item_id] for item_id in order if item_id in merged]
        tmp = INDEX_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(INDEX_PATH)


def append_audit(operation: str, intake_id: str | None, outcome: str, code: str | None = None,
                 attempt: int = 0, retry_at: str | None = None, operation_id: str | None = None,
                 idempotency_key: str | None = None) -> None:
    event = {"at": now_iso(), "operationId": operation_id, "operationType": operation,
             "idempotencyKey": idempotency_key, "intakeId": intake_id,
             "outcome": outcome, "code": code, "attempt": attempt,
             "retryAt": retry_at}
    with PRIVATE_FILE_LOCK:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_error_queue() -> list[dict]:
    ensure_private()
    try:
        value = json.loads(ERROR_QUEUE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_error_queue(items: list[dict]) -> None:
    with PRIVATE_FILE_LOCK:
        tmp = ERROR_QUEUE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(ERROR_QUEUE_PATH)


def enqueue_error(operation: str, intake_id: str | None, code: str, message: str,
                  payload: dict | None = None, attempt: int = 1,
                  operation_id: str | None = None, idempotency_key: str | None = None) -> dict:
    with PRIVATE_FILE_LOCK:
        items = load_error_queue()
        existing = next((x for x in items if x.get("idempotencyKey") == idempotency_key and x.get("status") in {"pending", "retrying", "failed"}), None)
        if existing:
            existing["lastError"] = safe_error_message(message)
            existing["updatedAt"] = now_iso()
            save_error_queue(items)
            return existing
        entry = {"id": "error-" + uuid.uuid4().hex, "operationId": operation_id, "operation": operation,
                 "idempotencyKey": idempotency_key, "intakeId": intake_id,
                 "status": "pending", "attempts": attempt, "maxAttempts": RETRY_LIMIT,
                 "errorCode": code, "lastError": safe_error_message(message), "createdAt": now_iso(),
                 "updatedAt": now_iso(), "nextRetryAt": None, "history": [], "payload": payload}
        items.insert(0, entry); save_error_queue(items)
        append_audit(operation, intake_id, "failed", code, attempt, operation_id=operation_id, idempotency_key=idempotency_key)
        return entry


def intake_operation_id(idempotency_key: str) -> str:
    return "op-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


def safe_error_message(message: str) -> str:
    value = re.sub(r"(?i)(password|passwd|token|secret|cookie|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", str(message))
    return value[:240]


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def media_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def media_identity(digest: str) -> str:
    return "media-" + digest[:24]


def existing_media_hashes(items: list[dict]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in items:
        for media in item.get("media", []) if isinstance(item, dict) else []:
            digest = media.get("sha256") if isinstance(media, dict) else None
            if digest and item.get("id"):
                hashes.setdefault(digest, item["id"])
    return hashes


class IntakeConflict(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def create_intake_record(fields: dict[str, str], files: list[tuple[str, bytes]], *, supplied_key: str = "", client_batch_id: str | None = None, failure: Callable[[str], None] | None = None) -> tuple[dict, bool]:
    """Atomically create one private intake record or replay an existing one.

    Parsing and basic size/type validation happen before this boundary. The
    lock deliberately covers the re-read, SHA deduplication, private media
    staging, metadata write, and index commit so ThreadingHTTPServer handlers
    cannot race on the JSON ledger.
    """
    key_material = supplied_key or json.dumps({
        "source": fields.get("source", "Cá nhân"),
        "sourceGroup": fields.get("sourceGroup", ""),
        "note": fields.get("note", ""),
        "clientBatchId": client_batch_id,
        "media": sorted(media_sha256(content) for _, content in files),
    }, ensure_ascii=False, sort_keys=True)
    idempotency_key = "intake:" + hashlib.sha256(key_material.encode("utf-8")).hexdigest()
    operation_id = intake_operation_id(idempotency_key)
    intake_id = "intake-" + operation_id[3:]

    with PRIVATE_FILE_LOCK:
        items = load_index()
        existing = next((x for x in items if x.get("idempotencyKey") == idempotency_key or x.get("id") == intake_id), None)
        if existing:
            return existing, True

        known_hashes = existing_media_hashes(items)
        request_hashes: set[str] = set()
        for filename, content in files:
            digest = media_sha256(content)
            if digest in request_hashes:
                raise IntakeConflict("DUPLICATE_MEDIA", f"File trùng trong cùng intake: {filename}.")
            if digest in known_hashes:
                raise IntakeConflict("DUPLICATE_MEDIA", f"File đã tồn tại trong intake {known_hashes[digest]}.")
            request_hashes.add(digest)

        folder = INBOX_ROOT / intake_id
        staging = INBOX_ROOT / f".{intake_id}.{uuid.uuid4().hex}.pending"
        if folder.exists():
            raise IntakeConflict("INTAKE_STORAGE_CONFLICT", "Thư mục intake đã tồn tại nhưng chưa có ledger record.")
        folder_created = False
        index_committed = False
        try:
            staging.mkdir(mode=0o700, parents=True, exist_ok=False)
            media = []
            for filename, content in files:
                target = staging / filename
                target.write_bytes(content)
                target.chmod(0o600)
                digest = media_sha256(content)
                media.append({"mediaId": media_identity(digest), "filename": target.name, "path": str((folder / target.name).relative_to(PRIVATE_ROOT)), "bytes": len(content), "sha256": digest})
            record = {"id": intake_id, "operationId": operation_id, "idempotencyKey": idempotency_key, "clientBatchId": client_batch_id, "source": fields.get("source", "Cá nhân"), "sourceGroup": fields.get("sourceGroup", ""), "sourceDate": now_iso(), "createdAt": now_iso(), "updatedAt": now_iso(), "status": "RECEIVED", "note": fields.get("note", ""), "media": media, "missingFields": ["title", "area", "price", "legal"], "conflicts": [], "draft": None, "channelApprovals": {c: False for c in CHANNELS}}
            (staging / "metadata.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (staging / "metadata.json").chmod(0o600)
            if failure: failure("before_folder_commit")
            staging.replace(folder)
            folder_created = True
            if failure: failure("before_index_commit")
            items.insert(0, record)
            save_index(items)
            index_committed = True
            if failure: failure("after_index_commit")
            return record, False
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            if folder_created and folder.exists() and not index_committed:
                shutil.rmtree(folder)
            raise


def password_ok(value: str) -> bool:
    try:
        expected = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(expected) and hmac.compare_digest(value, expected)


def login_page(message: str = "") -> bytes:
    notice = f'<p class="err">{html.escape(message)}</p>' if message else ""
    return (f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BMT Admin — Đăng nhập</title><style>body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#15243a;margin:0}}main{{max-width:420px;margin:12vh auto;padding:24px}}form{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:22px}}input,button{{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;font:inherit}}input{{border:1px solid #cbd5e1;margin:10px 0 14px}}button{{border:0;background:#0f2d5b;color:#fff;font-weight:800}}.err{{color:#b91c1c}}</style></head><body><main><h1>BMT Property Hub</h1><p>Admin nội bộ</p>{notice}<form method="post" action="/login"><label>Mật khẩu Admin</label><input name="password" type="password" autocomplete="current-password" required><button>Đăng nhập</button></form></main></body></html>''').encode("utf-8")


EVIDENCE_UI = '''<section id="owner-evidence" class="card" style="margin-top:16px"><p class="kicker">OWNER EVIDENCE · PRIVATE</p><h2>Evidence cho review hiện tại</h2><p class="muted">Review: review-6c508fe1ab9c0638a882a604</p><p id="evidence-missing" class="err">Đang kiểm tra trường còn thiếu...</p><form id="evidence-form"><label>Source ID</label><input name="source_id" required placeholder="Owner nhập source thật"><label>Claim được source hỗ trợ</label><textarea name="claim" required placeholder="Owner nhập claim thật"></textarea><label>Rights của citation</label><select name="citation_rights" required><option value="">Chọn rights</option><option>COMPANY</option><option>PERSONAL</option><option>APPROVED</option></select><label>Rights cấp review</label><select name="rights_status" required><option value="">Chọn rights</option><option>COMPANY</option><option>PERSONAL</option><option>APPROVED</option></select><button type="submit">Lưu evidence revision</button><p id="evidence-msg" class="muted"></p></form><script>(async()=>{const id='review-6c508fe1ab9c0638a882a604',missing=document.querySelector('#evidence-missing'),msg=document.querySelector('#evidence-msg'),form=document.querySelector('#evidence-form');try{const r=await fetch('/api/reviews/'+id+'/evidence'),d=await r.json();missing.textContent=d.status==='PASS'?'Evidence đã có revision '+d.evidence_revision_id:'Thiếu: '+(d.missing_fields||[]).join(', ')}catch(e){missing.textContent='Không kiểm tra được evidence.'}form.onsubmit=async e=>{e.preventDefault();const f=new FormData(form),citation={source_id:f.get('source_id'),claim:f.get('claim'),rights_status:f.get('citation_rights')};if(!citation.source_id||!citation.claim||!citation.rights_status||!f.get('rights_status')){msg.className='err';msg.textContent='Cần nhập đủ citations và rights.';return}msg.className='muted';msg.textContent='Đang kiểm tra...';const r=await fetch('/api/reviews/'+id+'/evidence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({citations:[citation],rights_status:f.get('rights_status')})}),d=await r.json();msg.className=r.ok?'ok':'err';msg.textContent=r.ok?'Đã lưu evidence revision '+d.evidence_revision_id:(d.error||'Evidence bị chặn');if(r.ok) form.reset()}})();</script></section>'''.encode('utf-8')


PUBLIC_RELEASE_UI = '''<script>
(()=>{
const priorLoadReview=window.loadReview;
const stateLabels={NOT_APPROVED:'Chưa duyệt đăng Website',PUBLIC_ALLOWED:'Đã cho phép preview riêng · chưa public',PUBLIC_ALLOWED_OTHER_REVIEW:'Property đang gắn quyết định ở review khác',REVOKED:'Đã thu hồi quyền preview',STALE:'Revision cũ · cần duyệt lại',INELIGIBLE:'Chưa đủ điều kiện'};
const blockerLabels={GATE_A_APPROVAL_REQUIRED:'Cần Gate A APPROVED đúng revision.',PROPERTY_NOT_FOUND:'Không tìm thấy canonical Property Core.',PROPERTY_REVISION_REQUIRED:'Thiếu revision Property Core.',PUBLIC_FACTS_INCOMPLETE:'Thiếu facts bắt buộc đã xác nhận.',PUBLIC_PRICE_INVALID:'Giá không hợp lệ.',LEGAL_STATUS_UNCONFIRMED:'Pháp lý chưa được xác nhận.',TRANSACTION_TYPE_UNCONFIRMED:'Chưa xác nhận bán/thuê.',PUBLIC_STATUS_UNSUPPORTED:'Trạng thái tin chưa được hỗ trợ.',RENT_PERIOD_UNSUPPORTED:'Chỉ hỗ trợ giá thuê theo tháng.',PROVENANCE_APPEARANCE_REQUIRED:'Thiếu provenance nguồn.',OPEN_PROPERTY_CONFLICTS:'Còn xung đột dữ liệu chưa xử lý.',PUBLIC_MEDIA_RIGHTS_DENIED:'Ảnh/file chưa có quyền public_web được duyệt.',PUBLIC_MEDIA_REFERENCE_UNSAFE:'Ảnh chưa nằm trong thư mục asset công khai hợp lệ.',PREVIEW_TARGET_NOT_CONFIGURED:'Chưa cấu hình thư mục preview tạm an toàn.',PREVIEW_TARGET_UNSAFE:'Đích preview nằm ngoài vùng tạm an toàn.',PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW:'Đã có release hiện hành ở review khác.',PREVIEW_WITHDRAWAL_FAILED:'Không thu hồi được artifact preview cũ.'};
window.loadReview=async function(id){await priorLoadReview(id);const detail=document.querySelector('#review-detail');if(!detail)return;const reviewResponse=await fetch('/api/reviews/'+encodeURIComponent(id),{credentials:'same-origin'});const review=await reviewResponse.json();if(!reviewResponse.ok)return;const panel=document.createElement('section');panel.id='public-release';panel.className='item';panel.innerHTML='<h4>Duyệt công bố riêng</h4><p class="muted">Tách biệt Gate A. Chỉ tạo/cập nhật preview tạm; thao tác này chưa phát hành công khai.</p><p data-release-status>Đang kiểm tra...</p><p data-release-lineage class="muted"></p><p data-release-facts class="muted"></p><p data-release-media class="muted"></p><ul data-release-blockers class="err"></ul><div class="row"><button type="button" data-release-approve style="min-height:44px;flex:1 1 180px">Duyệt tạo preview Website</button><button type="button" data-release-revoke class="secondary" style="min-height:44px;flex:1 1 180px">Thu hồi preview</button></div><p data-release-message class="muted" role="status"></p>';
detail.append(panel);const statusNode=panel.querySelector('[data-release-status]'),lineage=panel.querySelector('[data-release-lineage]'),factsNode=panel.querySelector('[data-release-facts]'),mediaNode=panel.querySelector('[data-release-media]'),blockerList=panel.querySelector('[data-release-blockers]'),message=panel.querySelector('[data-release-message]'),approve=panel.querySelector('[data-release-approve]'),revoke=panel.querySelector('[data-release-revoke]');let current=null;
const refresh=async()=>{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/public-release',{credentials:'same-origin'});const data=await response.json();current=data;if(!response.ok){statusNode.textContent='Không tải được trạng thái công bố: '+(blockerLabels[data.error]||data.error||response.status);return}statusNode.textContent=stateLabels[data.state]||data.state;lineage.textContent='Gate A '+(data.gate_a_state||'UNKNOWN')+' · review '+id+' r'+data.review_revision+' · property '+(data.property_id||'—')+' · Core r'+data.property_revision;const f=data.canonical_facts||{};factsNode.textContent='Facts Core: '+[f.title,f.property_type,f.address,f.area,f.price,f.price_type,f.public_status,f.legal_status].map(x=>x==null?'—':String(x)).join(' · ');const m=data.media_summary||{};mediaNode.textContent='Media active: '+(m.active||0)+' · đủ quyền và asset public: '+(m.eligible||0)+' · bị chặn: '+(m.blocked||0);blockerList.replaceChildren(...(data.blockers||[]).map(code=>{const li=document.createElement('li');li.textContent=blockerLabels[code]||code;return li}));approve.disabled=!data.can_approve;revoke.disabled=!data.can_revoke};
const act=async action=>{if(!current)return;let reason='';if(action==='revoke'){reason=window.prompt('Lý do thu hồi quyền preview?')||'';if(!reason.trim())return}const verb=action==='approve'?'duyệt tạo preview riêng':'thu hồi preview';if(!window.confirm('Xác nhận '+verb+' cho đúng property/revision đang hiển thị?'))return;approve.disabled=true;revoke.disabled=true;message.className='muted';message.textContent='Đang xử lý...';const nonce=window.crypto?.randomUUID?window.crypto.randomUUID():Date.now()+'-'+Math.random().toString(16).slice(2);try{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/public-release/'+action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({property_id:current.property_id,expected_review_revision:current.review_revision,expected_property_revision:current.property_revision,idempotency_key:'admin-ui:'+action+':'+id+':'+nonce,reason})});const data=await response.json();if(!response.ok){const labels=(data.blockers||[]).map(code=>blockerLabels[code]||code);throw new Error(labels.join(' · ')||blockerLabels[data.error]||data.error||'HTTP '+response.status)}message.className='ok';message.textContent=action==='approve'?'Đã ghi nhận Owner release; preview đã cập nhật, chưa public.':'Đã thu hồi release; preview đã được gỡ.';await refresh()}catch(error){message.className='err';message.textContent=error.message;await refresh()}};
approve.onclick=()=>act('approve');revoke.onclick=()=>act('revoke');await refresh()};
})();
</script>'''.encode('utf-8')


def page() -> bytes:
    return """<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BMT Property Hub — Kho nguồn hàng cá nhân</title><style>
:root{font-family:system-ui,sans-serif;color:#15243a;background:#f8fafc}*{box-sizing:border-box}body{margin:0}header{background:#0f2d5b;color:#fff;padding:18px 20px}main{max-width:900px;margin:auto;padding:18px 16px 60px}.grid{display:grid;gap:16px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 8px 20px #0f2d5b0c}.kicker{font-size:.72rem;letter-spacing:.12em;font-weight:800;color:#2563eb}h1,h2{margin:0 0 10px}label{display:block;font-weight:700;font-size:.9rem;margin:10px 0 5px}input,textarea,select{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}textarea{min-height:90px}button{border:0;border-radius:10px;padding:11px 15px;background:#0f2d5b;color:#fff;font:inherit;font-weight:800;cursor:pointer}.secondary{background:#e8effd;color:#2563eb}.row{display:flex;gap:8px;flex-wrap:wrap}.muted{color:#64748b;font-size:.9rem}.status{display:inline-block;border-radius:999px;padding:4px 9px;background:#e8effd;color:#2563eb;font-size:.75rem;font-weight:800}.item{border-top:1px solid #e5e7eb;padding-top:12px;margin-top:12px}.result{padding:10px 0;border-bottom:1px solid #e5e7eb}.ok{color:#07845e}.err{color:#b91c1c;white-space:pre-wrap}@media(min-width:700px){.grid{grid-template-columns:1fr 1fr}}
</style></head><body><header><strong>Hải Sala - BĐS</strong><div>Kho nguồn hàng cá nhân · local-only</div></header><main><section id="ai9-static" class="card"><p class="kicker">AI9 TEST ONLY</p><h2>Kiểm thử fixture tổng hợp</h2><p class="muted">Chỉ dữ liệu giả; hệ thống sẽ tự dọn sau khi preview.</p><form method="post" action="/api/ai9/test-only"><button type="submit" class="secondary">Chạy AI9 TEST_ONLY</button></form><pre id="ai9-static-result" class="muted"></pre></section><section class="card"><p class="kicker">INTAKE</p><h1>Nhận nguồn mới</h1><p class="muted">Bản gốc được lưu ngoài public root. V1 chưa ghi vào Property Core và chưa gửi ra kênh nào.</p><form id="intake" enctype="multipart/form-data"><label>Nguồn</label><select name="source"><option>Cá nhân</option><option>Zalo công ty</option><option>Khác</option></select><label>Nhóm/nguồn chi tiết</label><input name="sourceGroup" placeholder="Tên nhóm hoặc người cung cấp (nếu có)"><label>Thông tin sơ bộ</label><textarea name="note" placeholder="Giá, khu vực, pháp lý... chỉ ghi điều nguồn cung cấp"></textarea><label>Ảnh, video, ghi âm hoặc file gốc</label><input name="media" type="file" multiple accept="image/*,video/*,audio/*,.txt,.json,.md"><br><br><button type="submit">Lưu intake</button></form><p id="intake-msg"></p></section><section class="card"><p class="kicker">SEARCH</p><h2>Tra cứu listing thật</h2><form id="search"><input name="q" placeholder="Ví dụ: Thôn 8 sổ riêng 2 tỷ"><br><br><button class="secondary" type="submit">Tìm trong Property Core</button></form><div id="results"></div></section><section class="card"><p class="kicker">REVIEW CENTER · PRIVATE</p><h2>Hàng chờ duyệt Gate A</h2><p class="muted">Phân tích nội bộ chỉ dành cho Admin. Sửa thông tin quan trọng sẽ làm review thành STALE_REVIEW và phải tạo lại phân tích.</p><div id="reviews" class="muted">Đang tải...</div><div id="review-detail"></div></section><section class="card"><p class="kicker">QUEUE</p><h2>Approval Queue</h2><div id="queue" class="muted">Đang tải...</div></section></main><script>
const $=s=>document.querySelector(s);const mojibake={'C�� nh��n':'Cá nhân','C�� nh��n':'Cá nhân','Zalo c��ng ty':'Zalo công ty','Kh��c':'Khác'};const displayText=s=>{const v=String(s??'');return mojibake[v]||(/[�]/.test(v)?'Nội dung tiếng Việt đã lưu':v);};const esc=s=>String(displayText(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const testCard=document.createElement('section');testCard.className='card';testCard.innerHTML='<p class="kicker">AI9 TEST ONLY</p><h2>Kiểm thử fixture tổng hợp</h2><p class="muted">Chỉ dữ liệu giả; hệ thống sẽ tự dọn sau khi preview.</p><button id="run-ai9-test" class="secondary">Chạy AI9 TEST_ONLY</button><pre id="ai9-test-result" class="muted"></pre>';document.querySelector('main').prepend(testCard);$('#run-ai9-test').onclick=async()=>{const out=$('#ai9-test-result'),form=new FormData();form.set('source','TEST_ONLY');form.set('sourceGroup','SYNTHETIC');form.set('note','TEST_ONLY synthetic AI9 fixture — không phải dữ liệu thật');out.textContent='Đang tạo fixture...';try{let r=await fetch('/api/intakes',{method:'POST',body:form}),d=await r.json();if(!r.ok)throw Error(d.error||'INTAKE_FAILED');const intakeId=d.id;out.textContent='Đang process '+intakeId+'...';r=await fetch('/api/intakes/'+encodeURIComponent(intakeId)+'/process',{method:'POST'});d=await r.json();if(!r.ok)throw Error(d.error||'PROCESS_FAILED');const reviewId=d.review_id;out.textContent='Đang chạy AI9 Preview '+reviewId+'...';r=await fetch('/api/ai9/preview/'+encodeURIComponent(reviewId));const preview=await r.json();const receipt={response_status:r.status,review_id:reviewId,layers:Object.fromEntries(Object.entries(preview.layers||{}).map(([k,v])=>[k,v?.status||null])),errors:preview.errors||[],mutated_property_store:preview.mutated_property_store,published:preview.published};out.textContent=JSON.stringify(receipt,null,2);await fetch('/api/intakes/'+encodeURIComponent(intakeId),{method:'DELETE',headers:{'X-Test-Fixture':'TEST_ONLY'}});out.textContent+= '\\nCLEANUP: DONE';}catch(e){out.textContent='TEST_ONLY FAILED: '+e.message}finally{if(intakeId){const cleanup=await fetch('/api/intakes/'+encodeURIComponent(intakeId),{method:'DELETE',headers:{'X-Test-Fixture':'TEST_ONLY'}});out.textContent+='\nCLEANUP: '+(cleanup.ok?'DONE':'FAILED HTTP '+cleanup.status)}}};
async function loadReviews(){const r=await fetch('/api/reviews');const d=await r.json();if(!r.ok){$('#reviews').textContent=d.error||'Không tải được review';return}$('#reviews').innerHTML=d.length?d.map(x=>`<div class="item"><button class="secondary" data-review="${esc(x.review_id)}">Mở</button> <b>${esc(x.state)}</b> · ${esc(x.source||'')} · ${esc(x.intake_id||'')} · revision ${x.review_revision}</div>`).join(''):'Chưa có review.';document.querySelectorAll('[data-review]').forEach(b=>b.onclick=()=>loadReview(b.dataset.review))}
async function loadReview(id){const r=await fetch('/api/reviews/'+encodeURIComponent(id));const x=await r.json();if(!r.ok){$('#review-detail').textContent=x.error;return}const ia=x.internal_analysis||{},ds=ia.deal_score||{};$('#review-detail').innerHTML=`<div class="item"><h3>${esc(x.state)} · revision ${x.review_revision}</h3><h4>Nguồn / provenance</h4><pre>${esc(JSON.stringify(x.candidate?.raw||x.candidate?.provenance||{},null,2))}</pre><h4>Property facts</h4><pre>${esc(JSON.stringify(x.candidate?.facts||{},null,2))}</pre><h4>Thiếu / chưa Owner xác nhận</h4><pre>${esc(JSON.stringify(x.candidate?.missing_fields||[],null,2))}</pre><h4>Market / Comparable / Valuation</h4><pre>${esc(JSON.stringify({snapshot:x.market_snapshot?.market_snapshot_id,comparables:x.comparable_set?.status,valuation:x.valuation?.valuation_summary||x.valuation},null,2))}</pre><h4>AI Internal Analysis · PRIVATE</h4><pre>${esc(JSON.stringify({score:ds, strengths:ia.strengths, risks:ia.weaknesses_risks, anomalies:ia.anomalies, missing:ia.missing_information, questions:ia.questions_to_verify, recommendation:ia.recommended_action, confidence:ia.confidence},null,2))}</pre><p class="err">${x.state==='STALE_REVIEW'?'Phân tích đã cũ; không thể duyệt.':''}</p><label>Sửa field</label><input id="review-field" placeholder="ví dụ transaction_type, price_vnd_per_month, house_type, bedrooms"><input id="review-value" placeholder="Giá trị mới"><button data-edit-review>SAVE REVIEW REVISION</button> <button data-approve-review ${x.state==='STALE_REVIEW'||x.state==='REJECTED'?'disabled':''}>DUYỆT GATE A</button> <button data-reject-review>BỎ / TỪ CHỐI</button><p id="review-msg"></p></div>`;document.querySelector('[data-edit-review]').onclick=()=>reviewAction(id,'edit',x.review_revision,{field:$('#review-field').value,value:$('#review-value').value,owner_id:'owner'});document.querySelector('[data-approve-review]').onclick=()=>reviewAction(id,'approve',x.review_revision,{decision:'CREATE_NEW_PROPERTY',idempotency_key:'ui:'+id+':'+x.review_revision,owner_id:'owner'});document.querySelector('[data-reject-review]').onclick=()=>reviewAction(id,'reject',x.review_revision,{reason:'Owner rejected',owner_id:'owner'})}
async function reviewAction(id,kind,revision,extra){const r=await fetch('/api/reviews/'+id+'/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:revision,...extra})});const d=await r.json();const gateErrors={MISSING_FIELDS_REQUIRE_OWNER:'Cần Owner bổ sung/xác nhận trường bắt buộc theo loại BĐS.',PROPERTY_TYPE_UNSUPPORTED:'Loại BĐS chưa có quy tắc Gate A.',RENT_PERIOD_NOT_SUPPORTED_BY_PROPERTY_CORE:'Property Core hiện chỉ lưu được giá thuê theo tháng; kỳ hạn này chưa thể duyệt.',SALE_PRICE_PERIOD_INVALID:'Giá bán không nhận chu kỳ thuê.'};$('#review-msg').textContent=r.ok?'Đã cập nhật: '+(d.state||'OK'):(gateErrors[d.error]||d.error||'Lỗi');loadReviews();if(r.ok)loadReview(id)}
async function load(){let r=await fetch('/api/intakes');let d=await r.json();$('#queue').innerHTML=d.length?d.map(x=>`<div class="item"><b>${esc(x.id)}</b> <span class="status">${esc(x.status)}</span><div>${esc(x.source)} · ${esc(x.createdAt)}</div><div class="muted">${x.media.length} media · ${esc(x.note||'Không có ghi chú')}</div></div>`).join(''):'Chưa có intake.'}
$('#intake').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;let m=$('#intake-msg');m.textContent='Đang lưu...';m.className='muted';try{let r=await fetch('/api/intakes',{method:'POST',body:new FormData(form),credentials:'same-origin'}),d=await r.json();if(r.status!==201)throw Error(d.error||`INTAKE_FAILED_HTTP_${r.status}`);m.className='ok';m.textContent='Đã lưu '+d.id+' · bản gốc nằm ngoài public root.';form.reset();load()}catch(x){m.className='err';m.textContent=x.message||'Không thể lưu intake.'}});
$('#search').onsubmit=async e=>{e.preventDefault();let q=new FormData(e.target).get('q');let r=await fetch('/api/search?q='+encodeURIComponent(q));let d=await r.json();$('#results').innerHTML=d.length?d.map(x=>`<div class="result"><b>${esc(x.title)}</b><br>${esc(x.slug)} · ${esc(x.source||'Chưa có nguồn')}<br>${esc(x.location||x.addressText||'')}</div>`).join(''):'Không có listing khớp dữ liệu thật.'};load();loadReviews();
</script></body></html>""".replace("out.textContent+='" + chr(10) + "CLEANUP:", "out.textContent+='\\\\nCLEANUP:").replace("AI9 TEST ONLY", "BỘ XỬ LÝ CONTENT · TEST_ONLY").replace("Nguồn / provenance", "Nguồn & quyền sử dụng").encode("utf-8")


ANALYTICS_CARD = '''<section id="owner-analytics" class="card" style="margin-top:16px" aria-labelledby="analytics-title"><p class="kicker">WEBSITE ACTIVITY · OWNER ONLY</p><h2 id="analytics-title">Hoạt động Website</h2><p class="muted">Chỉ là lượt sự kiện đã nhận; không suy ra số khách, cuộc gọi thành công hay lead. Không thu số điện thoại người truy cập.</p><div class="grid"><div><label for="analytics-from">Từ ngày</label><input id="analytics-from" type="date"></div><div><label for="analytics-to">Đến ngày</label><input id="analytics-to" type="date"></div><div><label for="analytics-property">Property ID (không bắt buộc)</label><input id="analytics-property" inputmode="text" maxlength="73" placeholder="property-…"></div><div><label for="analytics-type">Loại sự kiện</label><select id="analytics-type"><option value="">Tất cả</option><option value="page_view">page_view</option><option value="property_card_view">property_card_view</option><option value="property_detail_view">property_detail_view</option><option value="search_filter">search_filter</option><option value="contact_cta_click">contact_cta_click</option></select></div><div><label for="analytics-channel">Kênh CTA</label><select id="analytics-channel"><option value="">Tất cả</option><option value="call">call</option><option value="zalo">zalo</option><option value="other">other</option></select></div></div><br><button type="button" id="analytics-load">Tải báo cáo</button><p id="analytics-status" class="muted" role="status">Chưa tải báo cáo.</p><div id="analytics-results" aria-live="polite"></div></section>'''.encode("utf-8")

ANALYTICS_UI = '''<script>
(()=>{const byId=id=>document.getElementById(id),from=byId('analytics-from'),to=byId('analytics-to'),status=byId('analytics-status'),results=byId('analytics-results');const day=d=>{const x=new Date(d.getTime()-d.getTimezoneOffset()*60000);return x.toISOString().slice(0,10)};const today=new Date();to.value=day(today);const start=new Date(today);start.setDate(start.getDate()-6);from.value=day(start);const group=(title,rows,keys)=>{const section=document.createElement('section'),h=document.createElement('h3'),ul=document.createElement('ul');h.textContent=title;section.append(h);if(!rows.length){const li=document.createElement('li');li.textContent='Chưa có';ul.append(li)}for(const row of rows){const li=document.createElement('li');li.textContent=keys.map(k=>row[k]??'—').join(' · ');ul.append(li)}section.append(ul);results.append(section)};byId('analytics-load').onclick=async()=>{status.textContent='Đang tải...';results.replaceChildren();const q=new URLSearchParams({from:from.value,to:to.value});for(const [id,key] of [['analytics-property','property_id'],['analytics-type','event_type'],['analytics-channel','channel']]){const v=byId(id).value.trim();if(v)q.set(key,v)}try{const r=await fetch('/api/analytics/report?'+q,{credentials:'same-origin',cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||'HTTP '+r.status);const total=document.createElement('p');total.className='status';total.textContent='Tổng lượt sự kiện: '+d.total_events;results.append(total);group('Theo ngày',d.by_day,['date','count']);group('Theo bất động sản và hành vi',d.by_property,['property_id','event_type','count']);group('Theo loại sự kiện',d.by_event_type,['event_type','count']);group('Kênh CTA',d.by_channel,['channel','count']);group('Nguồn đã làm sạch',d.by_source,['source','count']);status.textContent='Báo cáo đã tải; retention '+d.retention_days+' ngày. Không có số khách duy nhất.'}catch(e){status.textContent='Không tải được báo cáo: '+e.message}}})();
</script>'''.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "BMTAdmin/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        if urlparse(getattr(self, "path", "")).path == "/api/analytics/events":
            return
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, value: object, status: int = 200, *, headers: dict[str, str] | None = None) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, header_value in (headers or {}).items():
            self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(body)

    def analytics_origin(self) -> str | None:
        observed = _origin_from_url(self.headers.get("Origin", ""), allow_path=False)
        return observed if observed and observed in analytics_allowed_origins() else None

    def analytics_event_post(self) -> None:
        origin = self.analytics_origin()
        cors = {"Vary": "Origin"}
        if not origin:
            self.send_json({"error": "ANALYTICS_ORIGIN_REJECTED"}, 403)
            return
        cors["Access-Control-Allow-Origin"] = origin
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_json({"error": "ANALYTICS_CONTENT_TYPE_REQUIRED"}, 415, headers=cors)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "ANALYTICS_LENGTH_INVALID"}, 400, headers=cors)
            return
        if length <= 0:
            self.send_json({"error": "ANALYTICS_BODY_REQUIRED"}, 400, headers=cors)
            return
        if length > 4096:
            self.send_json({"error": "ANALYTICS_PAYLOAD_TOO_LARGE"}, 413, headers=cors)
            return
        if not _analytics_rate_allowed():
            self.send_json({"error": "ANALYTICS_RATE_LIMITED"}, 429, headers={**cors, "Retry-After": "60"})
            return
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            result = get_analytics_store().ingest(payload, property_is_public=_analytics_property_is_public)
            self.send_json(result, 200 if result.get("duplicate") else 202, headers=cors)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"error": "ANALYTICS_JSON_INVALID"}, 400, headers=cors)
        except AnalyticsValidationError as exc:
            status = 409 if str(exc) == "ANALYTICS_EVENT_ID_CONFLICT" else 422
            if str(exc) == "ANALYTICS_PAYLOAD_TOO_LARGE":
                status = 413
            self.send_json({"error": str(exc)}, status, headers=cors)
        except (OSError, ValueError, sqlite3.Error):
            self.send_json({"error": "ANALYTICS_STORAGE_UNAVAILABLE"}, 503, headers=cors)

    def analytics_report(self) -> None:
        if not self.require_auth():
            return
        owner_id = self.owner_id()
        configured_owner = os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"
        if not owner_id or owner_id != configured_owner:
            self.send_json({"error": "OWNER_ANALYTICS_REPORT_REQUIRED"}, 403)
            return
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        allowed = {"from", "to", "property_id", "event_type", "channel"}
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            self.send_json({"error": "ANALYTICS_REPORT_FILTER_INVALID"}, 400)
            return
        today = datetime.now(timezone.utc).date()
        try:
            start_date = date.fromisoformat(query.get("from", [(today - timedelta(days=6)).isoformat()])[0])
            end_date = date.fromisoformat(query.get("to", [today.isoformat()])[0])
            result = get_analytics_store().summary(
                start_date=start_date, end_date=end_date,
                property_id=query.get("property_id", [None])[0] or None,
                event_type=query.get("event_type", [None])[0] or None,
                channel=query.get("channel", [None])[0] or None,
            )
            result["retention_days"] = get_analytics_store().retention_days
            self.send_json(result)
        except (ValueError, OSError, sqlite3.Error) as exc:
            code = str(exc) if str(exc).startswith("ANALYTICS_") else "ANALYTICS_REPORT_UNAVAILABLE"
            status = 400 if code.endswith(("INVALID", "REJECTED", "NOT_ALLOWED")) else 503
            self.send_json({"error": code}, status)

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path != "/api/analytics/events":
            self.send_error(404)
            return
        origin = self.analytics_origin()
        requested_method = self.headers.get("Access-Control-Request-Method", "").upper()
        requested_headers = {item.strip().lower() for item in self.headers.get("Access-Control-Request-Headers", "").split(",") if item.strip()}
        if not origin or requested_method != "POST" or requested_headers - {"content-type"}:
            self.send_json({"error": "ANALYTICS_CORS_REJECTED"}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def approved_properties(self) -> list[dict]:
        """Owner-only read model; canonical store is the sole source."""
        with PROPERTY_OPERATION_LOCK:
            center = self.review_center_or_error()
            if center is None:
                return []
            approved_ids = {
                (package.get("mutation_result") or {}).get("property_id")
                for package in center.packages.values()
                if package.get("state") == "APPROVED" and (package.get("decision") or {}).get("type") == "APPROVE"
            }
            result = []
            for prop in center.store.list_properties():
                if prop["property_id"] not in approved_ids or int(prop.get("property_revision") or 0) < 1:
                    continue
                result.append({
                    "property_id": prop["property_id"], "property_revision": prop["property_revision"],
                    "title": prop.get("title"), "property_type": prop.get("property_type"),
                    "address": prop.get("address"), "area": prop.get("area"), "width": prop.get("width"),
                    "length": prop.get("length"), "land_area": prop.get("land_area"), "price": prop.get("price"),
                    "legal_status": prop.get("legal_status"), "road_access": prop.get("road_access"),
                    "features": prop.get("features", []), "factual_description": prop.get("factual_description"),
                    "lifecycle_state": prop.get("lifecycle_state"), "updated_at": prop.get("updated_at"),
                })
            return result

    def runtime_json(self, diagnostic: bool = False) -> None:
        try:
            value = diagnostic_payload() if diagnostic else health_payload()
        except ValueError:
            self.send_json({"error": "CONFIG_INVALID"}, 500)
            return
        self.send_json(value)

    def remote_allowed(self) -> bool:
        remote = self.client_address[0]
        return remote in {"127.0.0.1", "::1", ALLOWED_REMOTE_IP}

    def session_id(self) -> str | None:
        cookie = SimpleCookie(getattr(self, "headers", {}).get("Cookie", ""))
        morsel = cookie.get("bmt_admin_session")
        if not morsel:
            return None
        sid = morsel.value
        expires = SESSIONS.get(sid, 0)
        if expires <= datetime.now(timezone.utc).timestamp():
            SESSIONS.pop(sid, None)
            SESSION_OWNERS.pop(sid, None)
            return None
        return sid

    def owner_id(self) -> str | None:
        sid = self.session_id()
        return SESSION_OWNERS.get(sid) if sid else None

    def authenticated(self) -> bool:
        return self.remote_allowed() and self.session_id() is not None

    def require_auth(self) -> bool:
        if not self.remote_allowed():
            self.send_json({"error": "Nguồn truy cập không được phép."}, 403)
            return False
        if self.session_id() is None:
            self.send_json({"error": "Cần đăng nhập Admin."}, 401)
            return False
        return True

    def require_security_config(self) -> bool:
        if security_config_error() is None:
            return True
        self.send_json({"error": "ADMIN_SECURITY_CONFIG_INVALID"}, 503)
        return False

    def trusted_request_origin(self) -> bool:
        expected = trusted_origins()
        observed = []
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        if origin:
            normalized = _origin_from_url(origin, allow_path=False)
            if normalized is None:
                return False
            observed.append(normalized)
        if referer:
            normalized = _origin_from_url(referer, allow_path=True)
            if normalized is None:
                return False
            observed.append(normalized)
        return bool(expected and observed and len(set(observed)) == 1 and observed[0] in expected)

    def require_trusted_origin(self) -> bool:
        if not self.require_security_config():
            return False
        if not self.trusted_request_origin():
            self.send_json({"error": "CSRF_ORIGIN_REJECTED"}, 403)
            return False
        return True

    def review_center_or_error(self) -> ReviewCenter | None:
        if REVIEW_CENTER is None:
            self.send_json({"error": "REVIEW_CENTER_NOT_CONFIGURED"}, 503)
            return None
        return REVIEW_CENTER

    def ai9_preview(self, review_id: str) -> None:
        center = self.review_center_or_error()
        if center is None:
            return
        try:
            package = center.view(review_id)
        except KeyError:
            self.send_json({"error": "REVIEW_NOT_FOUND"}, 404)
            return
        owner_confirmed_test = (
            os.environ.get("BMT_ENV") == "staging"
            and review_id == OWNER_CONFIRMED_TEST_REVIEW
            and self.headers.get("X-Owner-Confirmed-Test") == OWNER_CONFIRMED_TEST_COMMENT
        )
        try:
            preview = run_ai9_preview(
                package,
                owner_confirmed_test=owner_confirmed_test,
                owner_confirmation_scope=self.headers.get("X-Owner-Confirmed-Test", ""),
                environment=os.environ.get("BMT_ENV", ""),
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        if owner_confirmed_test:
            live = center.packages.get(review_id)
            if live is not None:
                live.setdefault("ai9_runs", []).append(preview)
                live["updated_at"] = preview["created_at"]
                center._persist(live)
        self.send_json(preview)

    def copy_studio_preview(self, ai_run_id: str, *, mode: str = "APPROVAL_READY") -> None:
        center = self.review_center_or_error()
        if center is None:
            return
        for package in center.packages.values():
            run = next((x for x in package.get("ai9_runs", []) if x.get("ai_run_id") == ai_run_id), None)
            if run is None:
                continue
            if mode == GENERATIVE_MODE:
                result = build_generative_internal_draft(
                    ai_run=run, facts=(package.get("candidate") or {}).get("facts") or {},
                    provenance_status=(run.get("layers", {}).get("provenance") or {}).get("status", "INSUFFICIENT_EVIDENCE"),
                    rights_status=(package.get("candidate") or {}).get("rights_status"), provider=None,
                    model=_AI_PROVIDER.model, usage_sink=_USAGE_LEDGER)
            else:
                result = build_copy_studio_preview(
                ai_run=run,
                facts=(package.get("candidate") or {}).get("facts") or {},
                provenance_status=(run.get("layers", {}).get("provenance") or {}).get("status", "INSUFFICIENT_EVIDENCE"),
                rights_status=(package.get("candidate") or {}).get("rights_status"),
                mode=mode,
                )
            if result["status"] == "PASS":
                existing = next((x for x in package.get("content_studio_previews", []) if x.get("ai_run_id") == ai_run_id), None)
                if existing:
                    result = existing
                else:
                    package.setdefault("content_studio_previews", []).append(result)
                    package["updated_at"] = result["created_at"]
                    center._persist(package)
            self.send_json({"preview_route": f"/api/copy-studio/preview/{ai_run_id}", **result})
            return
        self.send_json({"error": "AI_RUN_NOT_FOUND"}, 404)

    def content_batch_generate(self, ai_run_id: str) -> None:
        center = self.review_center_or_error()
        if center is None:
            return
        package = next((p for p in center.packages.values() if any(x.get("ai_run_id") == ai_run_id for x in p.get("ai9_runs", []))), None)
        if package is None:
            self.send_json({"error": "AI_RUN_NOT_FOUND"}, 404)
            return
        # The production provider is deliberately not injected here in Phase 2.
        result = build_admin_content_batch(package, ai_run_id, provider=_AI_PROVIDER if _CONTENT_LIVE_ENABLED else None)
        if result.get("status") != "PASS":
            self.send_json({"content_route": f"/api/copy-studio/generate/{ai_run_id}", **result}, 409)
            return
        existing = next((x for x in package.get("content_studio_batches", []) if x.get("generation_id") == result.get("generation_id")), None)
        if existing is None:
            package.setdefault("content_studio_batches", []).append(result)
            package["updated_at"] = now_iso()
            center._persist(package)
        self.send_json({"content_route": f"/api/copy-studio/generate/{ai_run_id}", **(existing or result)}, 201)

    def evidence_route(self, review_id: str, *, payload: dict[str, Any] | None = None) -> None:
        if os.environ.get("BMT_ENV") != "staging":
            self.send_json({"error": "WRONG_ENVIRONMENT"}, 403)
            return
        center = self.review_center_or_error()
        if center is None:
            return
        package = center.packages.get(review_id)
        if package is None:
            self.send_json({"error": "REVIEW_NOT_FOUND"}, 404)
            return
        revisions = package.get("evidence_revisions") or []
        if payload is None:
            current = revisions[-1] if revisions else None
            self.send_json({"review_id": review_id, "status": "PASS" if current else "BLOCKED", "missing_fields": [] if current else ["citations", "rights_status"], "evidence_revision_id": current.get("evidence_revision_id") if current else None, "evidence": current.get("evidence") if current else None})
            return
        try:
            evidence = validate_evidence_payload(payload)
        except ValueError as exc:
            self.send_json({"status": "BLOCKED", "error": str(exc)}, 400)
            return
        revision_id = f"evidence-revision-{review_id}-r{len(revisions) + 1}"
        entry = {"evidence_revision_id": revision_id, "review_id": review_id, "intake_id": package["candidate"]["intake_id"], "evidence": evidence, "created_at": now_iso(), "origin": "OWNER_CONFIRMED"}
        package.setdefault("evidence_revisions", []).append(entry)
        package["updated_at"] = entry["created_at"]
        center._persist(package)
        append_audit("review_evidence_create", review_id, "succeeded", None, 1, operation_id=f"op-{revision_id}", idempotency_key=revision_id)
        self.send_json({"status": "PASS", "review_id": review_id, "evidence_revision_id": revision_id, "intake_id": entry["intake_id"], "citations_count": len(evidence["citations"]), "rights_status": evidence["rights_status"]}, 201)

    def review_route_get(self, path: str) -> bool:
        center = self.review_center_or_error()
        if center is None: return True
        center._load_packages()
        if path == "/api/reviews":
            self.send_json([{"review_id": x["review_id"], "state": x["state"], "review_revision": x["review_revision"], "property_id": (x.get("property_intelligence") or {}).get("property_id"), "candidate_id": (x.get("candidate") or {}).get("candidate_id"), "intake_id": (x.get("candidate") or {}).get("intake_id"), "source": (x.get("candidate") or {}).get("source"), "updated_at": x["updated_at"]} for x in center.packages.values()]); return True
        release_match = re.fullmatch(r"/api/reviews/([^/]+)/public-release", path)
        if release_match:
            try:
                with PROPERTY_OPERATION_LOCK:
                    self.send_json(_public_release_snapshot(center, release_match.group(1), cleanup_stale_preview=True))
            except KeyError as exc:
                self.send_json({"error": str(exc.args[0])}, 404)
            except (OSError, ValueError, StagingArtifactError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc) or "PUBLIC_RELEASE_STATUS_FAILED"}, 503)
            return True
        match = re.fullmatch(r"/api/reviews/([^/]+)", path)
        if match:
            try: self.send_json(center.view(match.group(1)))
            except KeyError: self.send_json({"error": "REVIEW_NOT_FOUND"}, 404)
            return True
        return False

    def public_release_route_post(self, review_id: str, action: str, payload: dict[str, Any]) -> None:
        center = self.review_center_or_error()
        if center is None:
            return
        owner_id = self.owner_id()
        configured_owner = os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"
        if not owner_id or owner_id != configured_owner:
            self.send_json({"error": "OWNER_PUBLIC_APPROVAL_REQUIRED"}, 403)
            return
        if "owner_id" in payload:
            self.send_json({"error": "OWNER_ID_BODY_FORBIDDEN"}, 403)
            return
        try:
            expected_review_revision = int(payload["expected_review_revision"])
            expected_property_revision = int(payload["expected_property_revision"])
            property_id = str(payload["property_id"])
            idempotency_key = str(payload["idempotency_key"])
            reason = str(payload.get("reason") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
                raise ValueError("PUBLIC_RELEASE_IDEMPOTENCY_KEY_INVALID")
            if action == "revoke" and not reason:
                raise ValueError("PUBLIC_RELEASE_REASON_REQUIRED")
        except (KeyError, TypeError, ValueError) as exc:
            self.send_json({"error": str(exc) if str(exc) else "PUBLIC_RELEASE_REQUEST_INVALID"}, 400)
            return

        with PROPERTY_OPERATION_LOCK:
            try:
                snapshot = _public_release_snapshot(center, review_id)
                if snapshot["property_id"] != property_id:
                    self.send_json({"error": "PUBLIC_RELEASE_PROPERTY_MISMATCH"}, 409)
                    return
                if snapshot["review_revision"] != expected_review_revision or snapshot["property_revision"] != expected_property_revision:
                    self.send_json({"error": "STALE_PUBLIC_RELEASE_REVISION", "review_revision": snapshot["review_revision"], "property_revision": snapshot["property_revision"]}, 409)
                    return
                package = center.packages[review_id]
                previous_decision = next((item for item in package.get("public_release_decisions", []) if item.get("idempotency_key") == idempotency_key), None)
                if previous_decision and (previous_decision.get("action") != ("APPROVE_PUBLICATION" if action == "approve" else "REVOKE_PUBLICATION") or previous_decision.get("property_revision") != expected_property_revision or previous_decision.get("owner_id") != owner_id):
                    self.send_json({"error": "PUBLIC_RELEASE_IDEMPOTENCY_CONFLICT"}, 409)
                    return
                if action == "approve":
                    if snapshot["blockers"]:
                        self.send_json({"error": "PUBLIC_RELEASE_BLOCKED", "blockers": snapshot["blockers"]}, 422)
                        return
                    if not snapshot["can_approve"] and not previous_decision:
                        self.send_json({"error": "PUBLICATION_ALREADY_APPROVED"}, 409)
                        return
                    canonical_path, website_path = _public_preview_paths()
                    prior_canonical = canonical_path.read_bytes() if canonical_path.exists() else None
                    prior_website = website_path.read_bytes() if website_path.exists() else None
                    recorded = False
                    try:
                        center.record_public_release(
                            review_id, expected_revision=expected_review_revision, property_id=property_id,
                            owner_id=owner_id, action="APPROVE_PUBLICATION", idempotency_key=idempotency_key,
                        )
                        recorded = True
                        projection_result = build_public_projection(center.store, property_id, canonical_path)
                        artifact_result = write_staging_web_artifact(projection_result["projection"], website_path)
                    except Exception as exc:
                        rollback_ok = True
                        if recorded:
                            try:
                                center.record_public_release(
                                    review_id, expected_revision=expected_review_revision, property_id=property_id,
                                    owner_id=owner_id, action="REVOKE_PUBLICATION",
                                    idempotency_key=idempotency_key + ":rollback", reason="Preview generation failed",
                                )
                            except Exception:
                                rollback_ok = False
                        try:
                            _restore_preview_file(canonical_path, prior_canonical)
                            _restore_preview_file(website_path, prior_website)
                        except OSError:
                            rollback_ok = False
                        self.send_json({"error": "PUBLIC_RELEASE_PREVIEW_FAILED", "release_revoked": recorded and rollback_ok, "rollback": "PASS" if rollback_ok else "FAILED"}, 503)
                        return
                    self.send_json({
                        "status": "PREVIEW_READY", "published": False, "review_id": review_id,
                        "property_id": property_id, "review_revision": expected_review_revision,
                        "property_revision": expected_property_revision,
                        "decision_owner": owner_id,
                        "projection_id": projection_result["projection"]["projectionId"],
                        "preview_records": artifact_result["records"], "preview_removed": 0,
                    }, 201)
                    return

                if action == "revoke":
                    if not snapshot["can_revoke"] and not previous_decision:
                        self.send_json({"error": "PUBLIC_RELEASE_NOT_ACTIVE"}, 409)
                        return
                    center.record_public_release(
                        review_id, expected_revision=expected_review_revision, property_id=property_id,
                        owner_id=owner_id, action="REVOKE_PUBLICATION", idempotency_key=idempotency_key,
                        reason=reason,
                    )
                    removed = _withdraw_private_preview(property_id)
                    self.send_json({"status": "REVOKED", "published": False, "review_id": review_id,
                                    "property_id": property_id, "review_revision": expected_review_revision,
                                    "property_revision": expected_property_revision, "decision_owner": owner_id,
                                    "preview_removed": removed}, 200)
                    return
            except KeyError as exc:
                self.send_json({"error": str(exc.args[0])}, 404)
            except ValueError as exc:
                error = str(exc)
                status = 409 if error in {"STALE_REVIEW", "PUBLICATION_ALREADY_APPROVED", "PUBLICATION_NOT_ACTIVE_FOR_REVISION", "PUBLIC_RELEASE_IDEMPOTENCY_CONFLICT", "PUBLIC_RELEASE_PROPERTY_MISMATCH"} else 422
                self.send_json({"error": error}, status)
            except (OSError, ProjectionError, StagingArtifactError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc) or "PUBLIC_RELEASE_PREVIEW_FAILED"}, 503)

    def review_route_post(self, path: str, raw: bytes) -> bool:
        center = self.review_center_or_error()
        if center is None: return True
        try: payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError: self.send_json({"error": "INVALID_REVIEW_JSON"}, 400); return True
        release_match = re.fullmatch(r"/api/reviews/([^/]+)/public-release/(approve|revoke)", path)
        if release_match:
            if not isinstance(payload, dict):
                self.send_json({"error": "PUBLIC_RELEASE_REQUEST_INVALID"}, 400)
                return True
            self.public_release_route_post(release_match.group(1), release_match.group(2), payload)
            return True
        match = re.fullmatch(r"/api/reviews/([^/]+)/edit", path)
        try:
            # HTTP callers are authenticated before reaching this method. The
            # payload owner_id is retained only for direct in-process fixtures;
            # a real request always binds identity from the server session.
            request_owner = self.owner_id() or str(payload.get("owner_id") or "")
            if match:
                with PROPERTY_OPERATION_LOCK:
                    result = center.edit(match.group(1), expected_revision=int(payload["expected_revision"]), field=str(payload["field"]), value=payload.get("value"), owner_id=request_owner)
                self.send_json(result); return True
            match = re.fullmatch(r"/api/reviews/([^/]+)/approve", path)
            if match:
                with PROPERTY_OPERATION_LOCK:
                    result = center.approve(match.group(1), expected_revision=int(payload["expected_revision"]), owner_id=request_owner, decision=str(payload["decision"]), idempotency_key=str(payload["idempotency_key"]), target_property_id=payload.get("target_property_id"))
                    # A Gate A mutation may advance canonical revision. Withdraw
                    # any older website preview before the next public read can
                    # mistake that stale artifact for the current revision.
                    _public_release_snapshot(center, match.group(1), cleanup_stale_preview=True)
                self.send_json(result); return True
            match = re.fullmatch(r"/api/reviews/([^/]+)/reject", path)
            if match:
                with PROPERTY_OPERATION_LOCK:
                    result = center.reject(match.group(1), expected_revision=int(payload["expected_revision"]), owner_id=request_owner, reason=str(payload["reason"]))
                self.send_json(result); return True
        except KeyError as exc: self.send_json({"error": str(exc.args[0])}, 404); return True
        except ValueError as exc: self.send_json({"error": str(exc)}, 409 if str(exc) in {"STALE_REVIEW", "REVIEW_REJECTED", "REVIEW_ALREADY_APPROVED"} else 400); return True
        return False

    def login_response(self, message: str = "") -> None:
        body = login_page(message)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def session_cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if SECURE_COOKIE else ""
        return f"bmt_admin_session={value}; Max-Age={max_age}; HttpOnly; SameSite=Strict; Path=/{secure}"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.runtime_json(); return
        if path == "/api/diagnostic":
            self.runtime_json(diagnostic=True); return
        if path == "/login":
            if not self.remote_allowed():
                self.send_json({"error": "Nguồn truy cập không được phép."}, 403)
                return
            if not self.require_security_config():
                return
            self.login_response(); return
        if path == "/logout":
            if not self.require_trusted_origin():
                return
            sid = self.session_id()
            if sid: SESSIONS.pop(sid, None); SESSION_OWNERS.pop(sid, None)
            self.send_response(303); self.send_header("Location", "/login"); self.send_header("Set-Cookie", self.session_cookie("", 0)); self.end_headers(); return
        if path == "/":
            if not self.authenticated():
                if not self.remote_allowed():
                    self.send_json({"error": "Nguồn truy cập không được phép."}, 403)
                    return
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            body = page().replace(b'<section id="ai9-static" class="card">', b'<section id="ai9-static" class="card" hidden>', 1)
            body = body.replace(b"</main><script>", ANALYTICS_CARD + b"</main><script>", 1)
            body = body.replace(b'</script></body></html>', b'</script>' + PUBLIC_RELEASE_UI + EVIDENCE_UI + ANALYTICS_UI + b'</body></html>', 1)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif not self.require_auth():
            return
        elif path == "/api/analytics/report":
            self.analytics_report()
        elif path == "/api/intakes":
            self.send_json(load_index())
        elif path == "/api/audit":
            try:
                lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
                self.send_json([json.loads(line) for line in lines[-200:] if line.strip()])
            except (OSError, json.JSONDecodeError):
                self.send_json({"error": "AUDIT_LOG_UNAVAILABLE"}, 503)
        elif path == "/api/errors":
            self.send_json(load_error_queue())
        elif re.fullmatch(r"/api/usage/report/review-[a-f0-9]+", path):
            review_id = path.rsplit("/", 1)[-1]
            center = self.review_center_or_error()
            package = center.packages.get(review_id) if center else None
            if not package:
                self.send_json({"error": "REVIEW_NOT_FOUND"}, 404)
            else:
                candidate = package.get("candidate") or {}
                usage = ((candidate.get("intelligence") or {}).get("metadata") or {}).get("usage")
                self.send_json(_USAGE_LEDGER.report(review_id=review_id, intake_id=str(candidate.get("intake_id")), historical_usage=usage, model=str(((candidate.get("intelligence") or {}).get("metadata") or {}).get("model") or _AI_PROVIDER.model)))
        elif path == "/api/properties":
            self.send_json(self.approved_properties())
        elif re.fullmatch(r"/api/reviews/review-[a-f0-9]+/evidence", path):
            self.evidence_route(path.split("/")[3])
        elif path == "/api/reviews" or path.startswith("/api/reviews/"):
            self.review_route_get(path)
            return
        elif re.fullmatch(r"/api/properties/[^/]+", path):
            property_id = path.rsplit("/", 1)[-1]
            item = next((x for x in self.approved_properties() if x["property_id"] == property_id), None)
            self.send_json(item if item else {"error": "PROPERTY_NOT_FOUND"}, 200 if item else 404)
        elif re.fullmatch(r"/api/ai9/preview/[^/]+", path):
            self.ai9_preview(path.rsplit("/", 1)[-1])
        elif path == "/api/search":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0].lower().strip()
            try:
                records = json.loads(PUBLIC_DATA.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                records = []
            terms = [x for x in re.split(r"\s+", q) if x]
            result = []
            for item in records if isinstance(records, list) else []:
                hay = json.dumps(item, ensure_ascii=False).lower()
                if not terms or all(t in hay for t in terms):
                    result.append({k: item.get(k) for k in ("id", "slug", "title", "source", "location", "addressText")})
            self.send_json(result)
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        """Staging-only cleanup for explicitly identified synthetic fixtures."""
        if not self.require_trusted_origin():
            return
        if not self.require_auth():
            return
        property_match = re.fullmatch(r"/api/properties/(property-[a-f0-9]+)", urlparse(self.path).path)
        if property_match:
            if os.environ.get("BMT_ENV", "") != "staging":
                self.send_json({"error": "WRONG_ENVIRONMENT"}, 403)
                return
            if self.headers.get("X-Test-Property", "") != "TEST_ONLY":
                self.send_json({"error": "TEST_ONLY_PROVENANCE_REQUIRED"}, 403)
                return
            try:
                status = self.review_center_or_error().store.purge_test_only_property(
                    property_match.group(1),
                    owner_intent=self.headers.get("X-Owner-Purge-Intent", ""),
                    environment=os.environ.get("BMT_ENV", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 403)
                return
            append_audit("test_property_purge", property_match.group(1), "succeeded", None, 1,
                         operation_id=f"op-purge-{property_match.group(1)}",
                         idempotency_key=f"purge:{property_match.group(1)}")
            self.send_json({"property_id": property_match.group(1), "status": status})
            return
        match = re.fullmatch(r"/api/intakes/(intake-[a-f0-9-]+)", urlparse(self.path).path)
        if not match or self.headers.get("X-Test-Fixture", "") != "TEST_ONLY":
            self.send_json({"error": "TEST_ONLY_FIXTURE_REQUIRED"}, 400)
            return
        intake_id = match.group(1)
        items = load_index()
        record = next((x for x in items if x.get("id") == intake_id), None)
        if record is not None and record.get("source") != "TEST_ONLY":
            self.send_json({"error": "REAL_RECORD_PROTECTED"}, 403)
            return
        center = self.review_center_or_error()
        if center is None:
            return
        try:
            review_status = center.remove_test_only(intake_id)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 403)
            return
        if record is not None:
            save_index([x for x in items if x.get("id") != intake_id])
        folder = INBOX_ROOT / intake_id
        if folder.exists():
            if folder.is_dir() and folder.parent == INBOX_ROOT:
                shutil.rmtree(folder)
            else:
                self.send_json({"error": "FIXTURE_PATH_UNSAFE"}, 409)
                return
        append_audit("test_fixture_cleanup", intake_id, "succeeded", None, 1,
                     operation_id=f"op-cleanup-{intake_id}",
                     idempotency_key=f"cleanup:{intake_id}")
        self.send_json({"id": intake_id, "status": "REMOVED" if record or review_status == "REMOVED" else "ALREADY_ABSENT"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/analytics/events":
            self.analytics_event_post()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        is_retry_request = re.fullmatch(r"/api/errors/([^/]+)/retry", path)
        bodyless_paths = {"/api/auth/logout", "/api/ai9/test-only"}
        bodyless_process = bool(re.fullmatch(r"/api/intakes/[^/]+/process", path))
        bodyless_copy_studio = bool(re.fullmatch(r"/api/copy-studio/preview/ai-run-[a-f0-9]+", path))
        if (length <= 0 and not is_retry_request and path not in bodyless_paths and not bodyless_process and not bodyless_copy_studio) or length > MAX_BODY:
            self.send_json({"error": "Request rỗng hoặc vượt giới hạn 50MB."}, 413)
            return
        if not self.require_trusted_origin():
            return
        raw = self.rfile.read(length)
        evidence_match = re.fullmatch(r"/api/reviews/(review-[a-f0-9]+)/evidence", path)
        if evidence_match:
            if not self.require_auth():
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json({"status": "BLOCKED", "error": "INVALID_EVIDENCE_JSON"}, 400)
                return
            self.evidence_route(evidence_match.group(1), payload=payload if isinstance(payload, dict) else {})
            return
        copy_studio_match = re.fullmatch(r"/api/copy-studio/preview/(ai-run-[a-f0-9]+)", path)
        if copy_studio_match:
            if not self.require_auth():
                return
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json({"error": "INVALID_COPY_STUDIO_JSON"}, 400)
                return
            mode = payload.get("mode", "APPROVAL_READY") if isinstance(payload, dict) else "APPROVAL_READY"
            if mode not in {"INTERNAL_DRAFT", "APPROVAL_READY", "GENERATIVE_INTERNAL_DRAFT"}:
                self.send_json({"error": "COPY_STUDIO_MODE_INVALID"}, 400)
                return
            self.copy_studio_preview(copy_studio_match.group(1), mode=mode)
            return
        content_generate_match = re.fullmatch(r"/api/copy-studio/generate/(ai-run-[a-f0-9]+)", path)
        if content_generate_match:
            if not self.require_auth():
                return
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json({"error": "INVALID_CONTENT_GENERATION_JSON"}, 400)
                    return
                if not isinstance(payload, dict):
                    self.send_json({"error": "INVALID_CONTENT_GENERATION_JSON"}, 400)
                    return
            self.content_batch_generate(content_generate_match.group(1))
            return
        if path == "/api/ai9/test-only":
            center = self.review_center_or_error()
            if center is None:
                return
            fixture = {"source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY synthetic AI9 fixture"}
            record = None
            try:
                record, _ = create_intake_record(fixture, [], supplied_key="ai9-server-test-only:" + uuid.uuid4().hex, client_batch_id=None)
                package = process_intake(center.store, center, record, ai_gateway=AI_GATEWAY, operation_id="ai9-test-only")
                preview = run_ai9_preview(package)
                receipt = {"response_status": 200, "review_id": package["review_id"], "layers": {k: (v or {}).get("status") for k, v in preview.get("layers", {}).items()}, "errors": preview.get("errors", []), "mutated_property_store": preview.get("mutated_property_store"), "published": preview.get("published")}
            except Exception as exc:
                receipt = {"error": str(exc), "mutated_property_store": False, "published": False}
                diagnostics = getattr(exc, "diagnostics", None)
                if isinstance(diagnostics, dict) and diagnostics:
                    receipt["provider_diagnostics"] = diagnostics
            finally:
                if record:
                    try:
                        center.remove_test_only(record["id"])
                    except Exception:
                        pass
                    items = load_index()
                    save_index([x for x in items if x.get("id") != record["id"]])
                    folder = INBOX_ROOT / record["id"]
                    if folder.exists() and folder.parent == INBOX_ROOT:
                        shutil.rmtree(folder)
            self.send_json(receipt, 200); return
        if path == "/api/auth/login":
            correlation = AUTH_CORRELATION.record("auth_entry", path, 0, "request_received")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                AUTH_CORRELATION.record("auth_response", path, 400, "invalid_auth_json")
                self.send_json({"error": "INVALID_AUTH_JSON"}, 400); return
            if not self.remote_allowed():
                AUTH_DIAGNOSTIC.record_once("ALLOWLIST_DENIED")
                AUTH_CORRELATION.record("allowlist_result", path, 401, "ALLOWLIST_DENIED")
                AUTH_CORRELATION.record("auth_response", path, 401, "ALLOWLIST_DENIED")
                self.send_json({"error": "AUTH_FAILED"}, 401); return
            if not password_ok(str(payload.get("password", ""))):
                AUTH_DIAGNOSTIC.record_once("PASSWORD_REJECTED")
                AUTH_CORRELATION.record("password_result", path, 401, "PASSWORD_REJECTED")
                AUTH_CORRELATION.record("auth_response", path, 401, "PASSWORD_REJECTED")
                self.send_json({"error": "AUTH_FAILED"}, 401); return
            sid = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc).timestamp() + SESSION_TTL
            owner = os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"
            SESSIONS[sid] = expires_at; SESSION_OWNERS[sid] = owner
            AUTH_CORRELATION.record("session_response", path, 200, "PASS")
            self.send_json({"authenticated": True, "owner_id": owner, "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z")}, headers={"Set-Cookie": self.session_cookie(sid, SESSION_TTL)})
            return
        if path == "/api/auth/logout":
            sid = self.session_id()
            if sid: SESSIONS.pop(sid, None); SESSION_OWNERS.pop(sid, None)
            if not self.remote_allowed():
                self.send_json({"error": "Nguồn truy cập không được phép."}, 403); return
            self.send_json({"authenticated": False}, headers={"Set-Cookie": self.session_cookie("", 0)})
            return
        if path == "/login":
            if not self.remote_allowed():
                self.send_json({"error": "Nguồn truy cập không được phép."}, 403); return
            form = parse_qs(raw.decode("utf-8", "replace"))
            if not password_ok(form.get("password", [""])[0]):
                self.login_response("Mật khẩu không đúng hoặc password file không sẵn sàng."); return
            sid = secrets.token_urlsafe(32); SESSIONS[sid] = datetime.now(timezone.utc).timestamp() + SESSION_TTL; SESSION_OWNERS[sid] = os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"
            self.send_response(303); self.send_header("Location", "/"); self.send_header("Set-Cookie", self.session_cookie(sid, SESSION_TTL)); self.end_headers(); return
        if not self.require_auth():
            return
        if path.startswith("/api/reviews/"):
            self.review_route_post(path, raw)
            return
        retry_match = re.fullmatch(r"/api/errors/([^/]+)/retry", path)
        if retry_match:
            error_id = retry_match.group(1)
            items = load_error_queue()
            entry = next((x for x in items if x.get("id") == error_id), None)
            if not entry:
                self.send_json({"error": "ERROR_NOT_FOUND", "code": "ERROR_NOT_FOUND"}, 404); return
            if entry.get("status") == "succeeded":
                self.send_json(entry); return
            if int(entry.get("attempts", 0)) >= int(entry.get("maxAttempts", RETRY_LIMIT)):
                self.send_json({"error": "RETRY_LIMIT_REACHED", "code": "RETRY_LIMIT_REACHED"}, 409); return
            entry["status"] = "retrying"; entry["attempts"] = int(entry.get("attempts", 0)) + 1; entry["updatedAt"] = now_iso()
            save_error_queue(items)
            append_audit("error_retry", entry.get("intakeId"), "retrying", entry.get("errorCode"), entry["attempts"], operation_id=entry.get("operationId"), idempotency_key=entry.get("idempotencyKey"))
            try:
                if entry.get("operation") != "intake_index_write":
                    raise ValueError("UNSUPPORTED_RETRY_OPERATION")
                payload = entry.get("payload") or {}
                record = payload.get("record")
                if not isinstance(record, dict) or not record.get("id"):
                    raise ValueError("RETRY_PAYLOAD_INVALID")
                current = load_index()
                if not any(x.get("id") == record["id"] for x in current):
                    current.insert(0, record); save_index(current)
                entry["status"] = "succeeded"; entry["lastError"] = None; entry["nextRetryAt"] = None; entry["updatedAt"] = now_iso()
                entry["history"].append({"at": now_iso(), "attempt": entry["attempts"], "status": "succeeded"})
                save_error_queue(items); append_audit("error_retry", entry.get("intakeId"), "succeeded", None, entry["attempts"], operation_id=entry.get("operationId"), idempotency_key=entry.get("idempotencyKey"))
                self.send_json(entry); return
            except Exception:
                entry["status"] = "failed" if entry["attempts"] >= int(entry.get("maxAttempts", RETRY_LIMIT)) else "pending"
                entry["lastError"] = "RETRY_FAILED"; entry["updatedAt"] = now_iso()
                entry["history"].append({"at": now_iso(), "attempt": entry["attempts"], "status": entry["status"]})
                save_error_queue(items); append_audit("error_retry", entry.get("intakeId"), "failed", "RETRY_FAILED", entry["attempts"], operation_id=entry.get("operationId"), idempotency_key=entry.get("idempotencyKey"))
                self.send_json({"error": "RETRY_FAILED", "code": "RETRY_FAILED", "status": entry["status"], "attempts": entry["attempts"]}, 500); return
        draft_match = re.fullmatch(r"/api/intakes/([^/]+)/draft", path)
        process_match = re.fullmatch(r"/api/intakes/([^/]+)/process", path)
        approval_match = re.fullmatch(r"/api/intakes/([^/]+)/approve/([^/]+)", path)
        if process_match:
            center = self.review_center_or_error()
            if center is None:
                return
            record = next((x for x in load_index() if x.get("id") == process_match.group(1)), None)
            if not record:
                self.send_json({"error": "Không tìm thấy intake."}, 404)
                return
            try:
                with PROPERTY_OPERATION_LOCK:
                    package = process_intake(center.store, center, record, ai_gateway=AI_GATEWAY, operation_id=record["id"])
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"intake_id": record["id"], "candidate_id": package["candidate"]["candidate_id"], "review_id": package["review_id"], "state": package["state"], "review_revision": package["review_revision"]}, 201)
            return
        if draft_match:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json({"error": "JSON draft không hợp lệ."}, 400)
                return
            items = load_index(); record = next((x for x in items if x.get("id") == draft_match.group(1)), None)
            if not record:
                self.send_json({"error": "Không tìm thấy intake."}, 404); return
            draft = {"title": payload.get("title", ""), "area": payload.get("area", ""), "price": payload.get("price"), "landArea": payload.get("landArea"), "legal": payload.get("legal", ""), "description": payload.get("description", ""), "confirmedByOwner": {}, "createdAt": now_iso(), "updatedAt": now_iso()}
            draft["missingFields"] = [k for k in ("title", "area", "price", "legal") if draft.get(k) in (None, "")]
            record["draft"] = draft; record["status"] = "DRAFT_NEEDS_REVIEW"; record["updatedAt"] = now_iso()
            save_index(items)
            folder = INBOX_ROOT / record["id"]; (folder / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); (folder / "draft.json").chmod(0o600)
            append_audit("draft_save", record["id"], "succeeded", None, 1, operation_id=f"op-{record['id']}-draft", idempotency_key=f"{record['id']}:draft")
            self.send_json({"id": record["id"], "status": record["status"], "draft": draft}, 201); return
        if approval_match:
            channel = approval_match.group(2)
            if channel not in CHANNELS:
                self.send_json({"error": "Kênh không hợp lệ."}, 400); return
            if channel == "public_web":
                self.send_json({"error": "Public Web promotion đang khóa trong V1; chưa ghi Property Core."}, 409); return
            items = load_index(); record = next((x for x in items if x.get("id") == approval_match.group(1)), None)
            if not record:
                self.send_json({"error": "Không tìm thấy intake."}, 404); return
            record.setdefault("channelApprovals", {})[channel] = True; record["updatedAt"] = now_iso(); save_index(items)
            append_audit("channel_approval", record["id"], "succeeded", None, 1, operation_id=f"op-{record['id']}-approve-{channel}", idempotency_key=f"{record['id']}:approve:{channel}")
            self.send_json({"id": record["id"], "channel": channel, "approved": True}); return
        if path != "/api/intakes":
            self.send_json({"error": "Endpoint không tồn tại."}, 404)
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            try:
                fields, files = parse_multipart_intake(raw, content_type)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
        else:
            fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            files = []
        if not fields.get("note", "").strip() and not files:
            self.send_json({"error": "Cần có thông tin sơ bộ hoặc ít nhất một file."}, 400)
            return
        for filename, content in files:
            if len(content) > MAX_FILE:
                self.send_json({"error": f"File {filename} vượt giới hạn 25MB."}, 413)
                return
            if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
                self.send_json({"error": f"Loại file không được hỗ trợ: {filename}"}, 400)
                return
        supplied_key = self.headers.get("X-Idempotency-Key", "").strip()
        client_batch_id = (self.headers.get("X-Client-Batch-ID", "").strip() or fields.get("clientBatchId", "").strip() or None)
        try:
            record, replay = create_intake_record(fields, files, supplied_key=supplied_key, client_batch_id=client_batch_id)
        except IntakeConflict as exc:
            self.send_json({"error": str(exc), "code": exc.code}, 200 if exc.code == "INTAKE_REPLAY" else 409)
            return
        except OSError:
            idempotency_key = "intake:" + hashlib.sha256((supplied_key or json.dumps({"source": fields.get("source", "Cá nhân"), "sourceGroup": fields.get("sourceGroup", ""), "note": fields.get("note", ""), "clientBatchId": client_batch_id, "media": sorted(media_sha256(content) for _, content in files)}, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
            operation_id = intake_operation_id(idempotency_key)
            intake_id = "intake-" + operation_id[3:]
            queue_entry = enqueue_error("intake_index_write", intake_id, "INDEX_WRITE_FAILED", "Không thể cập nhật index intake.", operation_id=operation_id, idempotency_key=idempotency_key)
            self.send_json({"error": "INTAKE_QUEUED_FOR_RETRY", "code": "INTAKE_QUEUED_FOR_RETRY", "errorId": queue_entry["id"]}, 503)
            return
        intake_id = record["id"]
        operation_id = record["operationId"]
        idempotency_key = record["idempotencyKey"]
        append_audit("intake_create", intake_id, "succeeded", None, 1, operation_id=operation_id, idempotency_key=idempotency_key)
        self.send_json(record, 200 if replay else 201)


def main() -> None:
    global HOST
    if security_config_error() is not None:
        raise SystemExit("Admin security configuration is not ready")
    ensure_private()
    validate_runtime_lock()
    if REVIEW_CENTER is None:
        configure_review_center(ReviewCenter(PropertyStore()))
    if not HOST or not ALLOWED_REMOTE_IP or not PASSWORD_FILE.exists():
        raise SystemExit("Admin safety prerequisites are not configured")
    print(f"BMT Admin listening on http://{HOST}:{PORT}/", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
