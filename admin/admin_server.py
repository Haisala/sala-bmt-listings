#!/usr/bin/env python3
"""Local-only BMT Property Hub intake/admin service.

This service intentionally keeps intake media and drafts outside the public
website root. It never writes Property Core in this V1; public promotion is a
separate, explicit boundary for a later owner-approved step.
"""
from __future__ import annotations

import html
import hashlib
import http.client
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
import unicodedata
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
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse, urlsplit
from property_memory.intake_review import process_intake
from property_memory.intake_adapter import KNOWN_FIELDS, to_candidate
from property_memory.providers.openai_provider import OpenAIPropertyProvider
from property_memory.ai_gateway import AIGateway
from property_memory.review_center import ReviewCenter, public_release_id
from property_memory.projection_builder import (ProjectionError, _is_public_asset_ref, _media_rights_allowed,
                                                _slug, build_public_projection, build_public_confirmation_preview)
from property_memory.content_channels import build_channel_drafts, channel_registry
from property_memory.staging_artifact_writer import StagingArtifactError, withdraw_staging_web_record, write_staging_web_artifact
from property_memory.ai9_layers import OWNER_CONFIRMED_TEST_COMMENT, OWNER_CONFIRMED_TEST_REVIEW, run_ai9_preview, run_gate_b, approve_gate_b
from property_memory.content_studio import build_copy_studio_preview, build_generative_internal_draft, build_generative_content_batch, GENERATIVE_MODE
try:
    from .one_touch_ui import HOME_CSS, HOME_HTML, HOME_SCRIPT, MISSING_FIELDS_MARKUP
except ImportError:  # Running admin_server.py as the service entrypoint.
    from one_touch_ui import HOME_CSS, HOME_HTML, HOME_SCRIPT, MISSING_FIELDS_MARKUP
from property_memory.evidence import validate_evidence_payload
from property_memory.keychain import CredentialError, resolve_openai_key
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
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
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
_CREDENTIAL_PREFLIGHT_LOCK = threading.Lock()
_CREDENTIAL_PREFLIGHT_LAST_AT = 0.0
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


def _analytics_property_is_public(property_id: str, publication_id: str) -> bool:
    center = REVIEW_CENTER
    if center is None:
        return False
    try:
        center._load_packages()
        package, decision = _latest_public_release(center, property_id)
        if not package or not decision or decision.get("action") != "APPROVE_PUBLICATION":
            return False
        if (decision.get("publication_id") or public_release_id(decision)) != publication_id:
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
    if os.environ.get("BMT_ENV") == "staging":
        staging_root_value = os.environ.get("BMT_ADMIN_STAGING_ROOT", "").strip()
        if not staging_root_value:
            raise ValueError("PREVIEW_TARGET_UNSAFE")
        staging_root_path = Path(staging_root_value).expanduser()
        if staging_root_path.is_symlink() or not staging_root_path.is_dir():
            raise ValueError("PREVIEW_TARGET_UNSAFE")
        if staging_root_path.stat().st_mode & 0o077:
            raise ValueError("PREVIEW_TARGET_UNSAFE")
        expected = staging_root_path.resolve() / "public-preview"
        if root != expected or root.is_symlink() or root == PROJECT_ROOT or PROJECT_ROOT in root.parents:
            raise ValueError("PREVIEW_TARGET_UNSAFE")
        if root.exists() and (not root.is_dir() or root.stat().st_mode & 0o077):
            raise ValueError("PREVIEW_TARGET_UNSAFE")
        return root / "canonical" / "projections.json", root / "data" / "listings.json"
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
    content = package.get("selected_content") or {}
    channel_approval = content.get("channel_approval") if isinstance(content.get("channel_approval"), dict) else {}
    if content.get("content_variant_id") and (channel_approval.get("channel") != "public_web"
            or channel_approval.get("content_variant_id") != content.get("content_variant_id")
            or int(channel_approval.get("content_revision") or 0) != int(content.get("content_revision") or 0)
            or channel_approval.get("status") != "OWNER_APPROVED_DRAFT"):
        blockers.append("WEBSITE_CONTENT_CHANNEL_APPROVAL_REQUIRED")
    prop = center.store.get(property_id) if property_id else None
    if prop is None:
        blockers.append("PROPERTY_NOT_FOUND")
        property_revision = 0
    else:
        property_revision = int(prop.get("property_revision") or 0)
        # Gate B content is optional for First Web. When present, the projection
        # builder validates its approval and revision linkage; without it, the
        # public DTO is built from approved canonical Property Core facts.
        if property_revision < 1:
            blockers.append("PROPERTY_REVISION_REQUIRED")
        required = ("title", "property_type", "address", "area", "price")
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
        if not active_media:
            blockers.append("PUBLIC_MEDIA_REQUIRED")
        if content.get("content_variant_id") and media_summary["eligible"] == 0:
            blockers.append("PUBLIC_CONTENT_MEDIA_REQUIRED")
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
    if active and blockers and state != "STALE" and "PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW" not in blockers:
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
    confirmation_preview = None
    preview_media: list[dict[str, Any]] = []
    if can_approve and prop is not None:
        try:
            confirmation_preview = build_public_confirmation_preview(
                center.store, property_id, public_content=content if content.get("content_variant_id") else None)
            preview_media = [
                {"media_id": hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24],
                 "media_ref": ref, "rights_status": "APPROVED_PUBLIC"}
                for ref in confirmation_preview.get("images", [])
            ]
        except (ProjectionError, KeyError, TypeError, ValueError):
            blockers.append("PUBLIC_CONFIRMATION_PREVIEW_INVALID")
            can_approve = False
            confirmation_preview = None
    return {
        "review_id": review_id, "review_revision": int(package.get("review_revision") or 0),
        "gate_a_state": package.get("state"), "property_id": property_id or None,
        "property_revision": property_revision, "state": state,
        "canonical_facts": canonical_facts, "media_summary": media_summary,
        "confirmation_preview": confirmation_preview, "confirmation_media": preview_media,
        "selected_content": {k: content.get(k) for k in ("content_batch_id", "content_variant_id", "content_revision", "title") if content.get(k)},
        "selected_content_channel_approved": bool(channel_approval.get("channel") == "public_web"
            and channel_approval.get("content_variant_id") == content.get("content_variant_id")
            and int(channel_approval.get("content_revision") or 0) == int(content.get("content_revision") or 0)
            and channel_approval.get("status") == "OWNER_APPROVED_DRAFT"),
        "environment": os.environ.get("BMT_ENV", "unknown"),
        "publication_mode": "STAGING_PUBLIC" if os.environ.get("BMT_ENV") == "staging" else "PRIVATE_PREVIEW",
        "release_action": latest_release.get("action") if belongs_to_review and latest_release else None,
        "release_owner_id": latest_release.get("owner_id") if belongs_to_review and latest_release else None,
        "publication_id": (latest_release.get("publication_id") or public_release_id(latest_release)) if belongs_to_review and own_active else None,
        "release_review_revision": latest_release.get("review_revision") if belongs_to_review and latest_release else None,
        "blockers": list(dict.fromkeys(blockers)), "can_approve": can_approve,
        "can_revoke": can_revoke, "preview_removed": preview_removed,
    }



def _media_rights_snapshot(center: ReviewCenter, review_id: str) -> dict[str, Any]:
    package = center.packages.get(review_id)
    if package is None:
        raise KeyError("REVIEW_NOT_FOUND")
    property_id = str((package.get("mutation_result") or {}).get("property_id") or "")
    appearances = []
    for appearance in center.store.list_source_appearances(property_id) if property_id else []:
        media = [x for x in center.store.list_media_history(property_id)
                 if x.get("active") and x.get("appearance_id") == appearance["appearance_id"]]
        right = center.store.connection.execute(
            "SELECT rights_status, approved FROM rights_refs WHERE property_id=? AND appearance_id=? AND channel='public_web' ORDER BY rowid DESC LIMIT 1",
            (property_id, appearance["appearance_id"])).fetchone()
        appearances.append({"appearance_id": appearance["appearance_id"], "media_count": len(media),
                            "rights_status": right["rights_status"] if right else None,
                            "approved": bool(right["approved"]) if right else False})
    return {"review_id": review_id, "property_id": property_id or None, "appearances": appearances}


def _approve_appearance_media(center: ReviewCenter, review_id: str, payload: dict[str, Any], owner_id: str) -> dict[str, Any]:
    if os.environ.get("BMT_ENV") != "staging":
        raise ValueError("STAGING_ONLY")
    if not owner_id or owner_id != (os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"):
        raise PermissionError("OWNER_MEDIA_RIGHTS_REQUIRED")
    package = center.packages.get(review_id)
    if package is None:
        raise KeyError("REVIEW_NOT_FOUND")
    property_id = str((package.get("mutation_result") or {}).get("property_id") or "")
    if payload.get("property_id") not in (None, property_id):
        raise ValueError("PROPERTY_ID_MISMATCH")
    appearance_id = str(payload.get("appearance_id") or "")
    status = str(payload.get("rights_status") or "")
    try: expected_revision = int(payload["expected_property_revision"])
    except (KeyError, TypeError, ValueError) as exc: raise ValueError("PROPERTY_REVISION_REQUIRED") from exc
    prop = center.store.get(property_id) if property_id else None
    decision = package.get("decision") or {}
    if not prop or package.get("state") != "APPROVED" or decision.get("type") != "APPROVE" or int(decision.get("revision", -1)) != int(package.get("review_revision", -2)):
        raise ValueError("GATE_A_APPROVAL_REQUIRED")
    if int(prop.get("property_revision") or 0) != expected_revision: raise ValueError("STALE_PROPERTY_REVISION")
    if status not in {"PERSONAL", "COMPANY"}: raise ValueError("MEDIA_RIGHTS_STATUS_INVALID")
    appearance=center.store.get_source_appearance(appearance_id)
    intake_id=str((package.get("candidate") or {}).get("intake_id") or "")
    if not appearance or appearance.get("property_id") != property_id or appearance.get("intake_id") != intake_id:
        raise ValueError("APPEARANCE_NOT_IN_REVIEW")
    history=center.store.list_media_history(property_id)
    matching=[x for x in history if x.get("active") and x.get("appearance_id")==appearance_id]
    public_active=[x for x in matching if _is_public_asset_ref(str(x.get("media_ref") or ""), _slug(str(prop.get("title") or property_id)))]
    private_active=[x for x in matching if x not in public_active]
    prior=center.store.connection.execute("SELECT rights_status,approved FROM rights_refs WHERE property_id=? AND appearance_id=? AND channel='public_web' ORDER BY rowid DESC LIMIT 1",(property_id,appearance_id)).fetchone()
    if public_active and not private_active and prior and prior["rights_status"]==status and bool(prior["approved"]) and all(hmac.compare_digest(hashlib.sha256((Path(os.environ["BMT_ADMIN_PUBLIC_ASSETS_ROOT"])/x["media_ref"]).read_bytes()).hexdigest(),str(x.get("media_sha256") or "")) for x in public_active):
        return {"status":"APPROVED","property_id":property_id,"property_revision":expected_revision,"appearance_id":appearance_id,"media_count":len(public_active),"rights_status":status,"published":False,"idempotent_replay":True}
    active=private_active
    if not active: raise ValueError("MEDIA_NOT_FOUND")
    staging_value=os.environ.get("BMT_ADMIN_STAGING_ROOT","").strip(); assets_value=os.environ.get("BMT_ADMIN_PUBLIC_ASSETS_ROOT","").strip()
    if not staging_value or not assets_value: raise ValueError("PUBLIC_ASSET_ROOT_NOT_CONFIGURED")
    staging=Path(staging_value).expanduser(); assets=Path(assets_value).expanduser()
    if staging.is_symlink() or not staging.is_dir() or staging.stat().st_mode & 0o077 or assets.is_symlink() or assets.resolve()!=staging.resolve()/"public-assets":
        raise ValueError("PUBLIC_ASSET_ROOT_UNSAFE")
    assets.mkdir(mode=0o700,parents=True,exist_ok=True)
    if assets.stat().st_mode & 0o077: raise ValueError("PUBLIC_ASSET_ROOT_UNSAFE")
    attrs=prop.get("attributes") if isinstance(prop.get("attributes"),dict) else {}
    slug=str(attrs.get("public_slug") or _slug(str(prop.get("title") or property_id)))
    if not re.fullmatch(r"[a-z0-9-]{1,120}",slug): raise ValueError("PUBLIC_SLUG_INVALID")
    dest_dir=assets/"assets"/"images"/slug; dest_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    if dest_dir.is_symlink() or not dest_dir.resolve().is_relative_to(assets.resolve()): raise ValueError("PUBLIC_ASSET_PATH_UNSAFE")
    declared={(str(x.get("path") or ""),str(x.get("sha256") or "")) for x in ((package.get("candidate") or {}).get("media") or []) if isinstance(x,dict)}
    promoted=[]
    for item in active:
        ref=str(item.get("media_ref") or ""); digest=str(item.get("media_sha256") or ""); parts=PurePosixPath(ref).parts
        if len(parts)!=3 or parts[0]!="inbox" or parts[1]!=intake_id or (ref,digest) not in declared or not re.fullmatch(r"[0-9a-f]{64}",digest):
            raise ValueError("PRIVATE_MEDIA_REFERENCE_UNSAFE")
        source=PRIVATE_ROOT.joinpath(*parts)
        if PRIVATE_ROOT.is_symlink() or INBOX_ROOT.is_symlink() or source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(INBOX_ROOT.resolve()): raise ValueError("PRIVATE_MEDIA_UNAVAILABLE")
        if source.stat().st_size > MAX_FILE: raise ValueError("MEDIA_FILE_TOO_LARGE")
        content=source.read_bytes()
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(),digest): raise ValueError("PRIVATE_MEDIA_HASH_MISMATCH")
        suffix=source.suffix.lower(); signatures={".jpg":content.startswith(b"\xff\xd8\xff"),".jpeg":content.startswith(b"\xff\xd8\xff"),".png":content.startswith(b"\x89PNG\r\n\x1a\n"),".webp":len(content)>=12 and content[:4]==b"RIFF" and content[8:12]==b"WEBP",".avif":len(content)>=12 and content[4:8]==b"ftyp" and content[8:12] in {b"avif",b"avis"}}
        if suffix not in signatures or not signatures[suffix]: raise ValueError("MEDIA_IMAGE_FORMAT_UNSUPPORTED")
        filename=digest[:32]+suffix; dest=dest_dir/filename
        if dest.exists():
            if dest.is_symlink() or not hmac.compare_digest(hashlib.sha256(dest.read_bytes()).hexdigest(),digest): raise ValueError("PUBLIC_ASSET_COLLISION")
        else:
            fd,tmp=tempfile.mkstemp(prefix=".media.",dir=str(dest_dir))
            try:
                with os.fdopen(fd,"wb") as out: out.write(content); out.flush(); os.fsync(out.fileno())
                os.chmod(tmp,0o600); os.replace(tmp,dest)
            except Exception:
                try: os.unlink(tmp)
                except FileNotFoundError: pass
                raise
        promoted.append({"source_event_id":str(item["event_id"]),"media_ref":f"assets/images/{slug}/{filename}","sha256":digest,"role":str(item.get("role") or "image")})
    rights_id="rights-"+hashlib.sha256(f"{property_id}:{appearance_id}:public_web:{expected_revision}:{status}".encode()).hexdigest()[:32]
    right=center.store.connection.execute("SELECT * FROM rights_refs WHERE rights_id=?",(rights_id,)).fetchone()
    if right:
        if (right["property_id"],right["appearance_id"],right["rights_status"],right["channel"],int(right["approved"]))!=(property_id,appearance_id,status,"public_web",1): raise ValueError("MEDIA_RIGHTS_IDEMPOTENCY_CONFLICT")
    else: center.store.add_rights(property_id,{"appearance_id":appearance_id,"rights_status":status,"channel":"public_web","approved":True},rights_id=rights_id)
    for item in promoted:
        event_id="media-public-"+hashlib.sha256(f"{property_id}:{appearance_id}:{item['sha256']}".encode()).hexdigest()[:24]
        old=center.store.connection.execute("SELECT * FROM property_media_history WHERE event_id=?",(event_id,)).fetchone()
        if old:
            if old["media_ref"]!=item["media_ref"] or old["media_sha256"]!=item["sha256"]: raise ValueError("PUBLIC_MEDIA_IDEMPOTENCY_CONFLICT")
        else:
            with center.store.connection: center.store.connection.execute("UPDATE property_media_history SET active=0 WHERE event_id=? AND property_id=?",(item["source_event_id"],property_id))
            center.store.append_media_observation(property_id,{"appearance_id":appearance_id,"media_ref":item["media_ref"],"media_sha256":item["sha256"],"role":item["role"],"rights_status":status,"active":True},event_id=event_id)
        prov_id="provenance-"+hashlib.sha256(f"{property_id}:{appearance_id}:{item['sha256']}:public_web".encode()).hexdigest()[:32]
        if not center.store.get_provenance(prov_id): center.store.add_provenance(property_id,appearance_id,{"field_name":"public_media","information_origin":"OWNER_CONFIRMED","intake_id":intake_id,"media_ref":item["media_ref"],"media_sha256":item["sha256"]},provenance_id=prov_id)
    return {"status":"APPROVED","property_id":property_id,"property_revision":expected_revision,"appearance_id":appearance_id,"media_count":len(promoted),"rights_status":status,"published":False}


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


def remove_index_record(intake_id: str) -> None:
    """Atomically remove one intake; the create/update merge path cannot delete."""
    ensure_private()
    with PRIVATE_FILE_LOCK:
        try:
            current = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = []
        if not isinstance(current, list):
            current = []
        filtered = [item for item in current if not isinstance(item, dict) or item.get("id") != intake_id]
        tmp = INDEX_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(filtered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def deduplicate_same_batch_images(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Keep the first byte-identical image in one POST; leave other files unchanged."""
    seen_images: set[str] = set()
    accepted: list[tuple[str, bytes]] = []
    duplicates: list[str] = []
    for filename, content in files:
        digest = media_sha256(content)
        if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
            if digest in seen_images:
                duplicates.append(filename)
                continue
            seen_images.add(digest)
        accepted.append((filename, content))
    return accepted, duplicates


def extract_preliminary_facts(note: str) -> dict[str, Any]:
    """Extract only narrow, literal patterns from Owner source text; never infer a title or legal status."""
    text = str(note or "")
    normalized = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    facts: dict[str, Any] = {}
    if re.search(r"\b(cho thue|thue|rent|rental)\b", folded):
        facts["transaction_type"] = "RENT"
    elif re.search(r"\b(ban|sale)\b", folded):
        facts["transaction_type"] = "SALE"
    if re.search(r"can ho|apartment", folded):
        facts["property_type"] = "APARTMENT"
    elif re.search(r"biet thu|nha pho|nha rieng|nha cap", folded):
        facts["property_type"] = "HOUSE"
    elif re.search(r"\bdat\b", folded):
        facts["property_type"] = "LAND"
    if "biet thu" in folded:
        facts["house_type"] = "BIET_THU"
    elif "nha pho" in folded:
        facts["house_type"] = "NHA_PHO"
    area = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:m²|m2|sqm|met vuong)(?:\b|(?=\s|$))", folded)
    if area:
        facts["area"] = area.group(1).replace(",", ".") + " m²"
    amount = re.search(r"(?:gia\s*(?:thue|ban)?\s*[:=]?\s*)?(\d+(?:[.,]\d+)?)\s*(ty|ti|trieu|tr|nghin|ngan)\b", folded)
    if amount:
        value = float(amount.group(1).replace(",", "."))
        unit = amount.group(2)
        multiplier = 1_000_000_000 if unit in {"ty", "ti"} else 1_000_000 if unit in {"trieu", "tr"} else 1_000
        amount_vnd = int(value * multiplier)
        transaction = facts.get("transaction_type")
        if transaction == "RENT" or re.search(r"/\s*thang|moi\s*thang|per\s*month", folded):
            facts["transaction_type"] = "RENT"
            facts["price_vnd_per_month"] = amount_vnd
            facts["price_cycle"] = "MONTH"
        elif transaction == "SALE":
            facts["price_vnd"] = amount_vnd
    address = re.search(r"(?:mat tien|duong|dia chi|tai khu vuc)\s+([^,;.!\n]+)", folded)
    if address:
        # Preserve source spelling by using the corresponding slice from the original string.
        start = address.start(1)
        facts["address"] = text[start:start + len(address.group(1))].strip()
    for name, pattern in (("bedrooms", r"(\d+)\s*(?:phong ngu|bedrooms?)"), ("bathrooms", r"(\d+)\s*(?:wc|phong tam|bathrooms?)")):
        match = re.search(pattern, folded)
        if match:
            facts[name] = int(match.group(1))
    return facts


def validate_evidence_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("CONTENT_EVIDENCE_INVALID")
    rights = str(value.get("rights_status") or "").strip().upper()
    citations = value.get("citations")
    if rights not in {"PERSONAL", "COMPANY", "APPROVED"} or not isinstance(citations, list) or not citations:
        raise ValueError("CONTENT_PREREQUISITES_REQUIRED")
    normalized = []
    for citation in citations:
        if not isinstance(citation, dict):
            raise ValueError("CONTENT_EVIDENCE_INVALID")
        source_id = str(citation.get("source_id") or "").strip()
        claim = str(citation.get("claim") or "").strip()
        citation_rights = str(citation.get("rights_status") or "").strip().upper()
        if (not source_id or len(source_id) > 160 or not claim or len(claim) > 1200
                or citation_rights not in {"PERSONAL", "COMPANY", "APPROVED"}):
            raise ValueError("CONTENT_EVIDENCE_INVALID")
        normalized.append({"source_id": source_id, "claim": claim, "rights_status": citation_rights})
    return {"rights_status": rights, "citations": normalized}


def intake_content_readiness(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    candidate = to_candidate(record)
    facts = candidate.get("facts") or {}
    missing: list[str] = []
    for key in ("title", "address", "area", "property_type"):
        if facts.get(key) in (None, ""):
            missing.append(key)
    transaction = str(facts.get("price_type") or facts.get("transaction_type") or "").strip().upper()
    if transaction not in {"SALE", "RENT"}:
        missing.append("transaction_type")
    amount_key = "price_vnd_per_month" if transaction == "RENT" else "price_vnd"
    amount = facts.get(amount_key)
    amount_ok = isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0
    if isinstance(amount, str):
        try:
            amount_ok = float(amount.strip().replace(",", ".")) > 0
        except ValueError:
            amount_ok = False
    if not amount_ok:
        missing.append(amount_key)
    if transaction == "RENT" and str(facts.get("price_cycle") or "").upper() != "MONTH":
        missing.append("price_cycle")
    kind = str(facts.get("property_type") or "").upper()
    if kind in {"HOUSE", "NHÀ", "NHA", "HOUSE_TYPE"}:
        for key in ("house_type", "bedrooms", "bathrooms"):
            if facts.get(key) in (None, ""):
                missing.append(key)
    evidence = record.get("ownerEvidence") if isinstance(record.get("ownerEvidence"), dict) else {}
    missing_evidence = [key for key in ("source_id", "claim", "rights_status") if not str(evidence.get(key) or "").strip()]
    if evidence.get("rights_status") not in {"PERSONAL", "COMPANY", "APPROVED"} and "rights_status" not in missing_evidence:
        missing_evidence.append("rights_status")
    return list(dict.fromkeys(missing)), missing_evidence


def create_intake_record(fields: dict[str, str], files: list[tuple[str, bytes]], *, supplied_key: str = "", client_batch_id: str | None = None, failure: Callable[[str], None] | None = None) -> tuple[dict, bool, dict]:
    """Atomically create one private intake record or replay an existing one.

    Parsing and basic size/type validation happen before this boundary. The
    lock deliberately covers the re-read, SHA deduplication, private media
    staging, metadata write, and index commit so ThreadingHTTPServer handlers
    cannot race on the JSON ledger.
    """
    received_count = len(files)
    files, duplicate_filenames = deduplicate_same_batch_images(files)
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
            return existing, True, {"received": received_count, "accepted": len(existing.get("media", [])),
                                    "duplicate_count": len(duplicate_filenames), "duplicates": duplicate_filenames}

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
            used_filenames: set[str] = set()
            for filename, content in files:
                stored_filename = filename
                if stored_filename in used_filenames:
                    source_path = Path(filename)
                    stem, suffix = source_path.stem, source_path.suffix
                    serial = 2
                    while stored_filename in used_filenames:
                        stored_filename = safe_name(f"{stem}-{serial}{suffix}")
                        serial += 1
                used_filenames.add(stored_filename)
                target = staging / stored_filename
                target.write_bytes(content)
                target.chmod(0o600)
                digest = media_sha256(content)
                media.append({"mediaId": media_identity(digest), "filename": filename, "storedFilename": stored_filename, "path": str((folder / stored_filename).relative_to(PRIVATE_ROOT)), "bytes": len(content), "sha256": digest})
            owner_evidence = {"source_id": fields.get("evidence_source_id", "").strip(),
                              "claim": fields.get("evidence_claim", "").strip(),
                              "rights_status": fields.get("evidence_rights", "").strip()}
            owner_evidence = owner_evidence if any(owner_evidence.values()) else None
            record = {"id": intake_id, "operationId": operation_id, "idempotencyKey": idempotency_key, "clientBatchId": client_batch_id, "source": fields.get("source", "Cá nhân"), "sourceGroup": fields.get("sourceGroup", ""), "sourceDate": now_iso(), "createdAt": now_iso(), "updatedAt": now_iso(), "inputRevision": 1, "status": "RECEIVED", "note": fields.get("note", ""), "ownerEvidence": owner_evidence, "ownerEvidenceRevision": 1 if owner_evidence else 0, "preliminaryFacts": extract_preliminary_facts(fields.get("note", "")), "ownerFacts": {}, "ownerFactsRevision": 0, "media": media, "missingFields": [], "conflicts": [], "draft": None, "channelApprovals": {c: False for c in CHANNELS}}
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
            return record, False, {"received": received_count, "accepted": len(media),
                                   "duplicate_count": len(duplicate_filenames), "duplicates": duplicate_filenames}
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
const stateLabels={NOT_APPROVED:'Chưa duyệt đăng Website',PUBLIC_ALLOWED:'Owner đã duyệt revision này',PUBLIC_ALLOWED_OTHER_REVIEW:'Property đang gắn quyết định ở review khác',REVOKED:'Đã thu hồi công bố',STALE:'Revision cũ · cần duyệt lại',INELIGIBLE:'Chưa đủ điều kiện'};
const blockerLabels={GATE_A_APPROVAL_REQUIRED:'Cần Gate A APPROVED đúng revision.',PROPERTY_NOT_FOUND:'Không tìm thấy canonical Property Core.',PROPERTY_REVISION_REQUIRED:'Thiếu revision Property Core.',PUBLIC_FACTS_INCOMPLETE:'Thiếu facts bắt buộc đã xác nhận, gồm diện tích.',PUBLIC_PRICE_INVALID:'Giá không hợp lệ.',LEGAL_STATUS_UNCONFIRMED:'Pháp lý chưa được xác nhận.',TRANSACTION_TYPE_UNCONFIRMED:'Chưa xác nhận bán/thuê.',PUBLIC_STATUS_UNSUPPORTED:'Trạng thái tin chưa được hỗ trợ.',RENT_PERIOD_UNSUPPORTED:'Chỉ hỗ trợ giá thuê theo tháng.',PROVENANCE_APPEARANCE_REQUIRED:'Thiếu provenance nguồn.',OPEN_PROPERTY_CONFLICTS:'Còn xung đột dữ liệu chưa xử lý.',PUBLIC_MEDIA_REQUIRED:'Cần ít nhất một ảnh cho Website.',PUBLIC_CONTENT_MEDIA_REQUIRED:'Content Website cần ít nhất một ảnh được duyệt quyền public.',PUBLIC_MEDIA_RIGHTS_DENIED:'Ảnh/file chưa có quyền public_web được duyệt.',PUBLIC_MEDIA_REFERENCE_UNSAFE:'Ảnh chưa nằm trong thư mục asset công khai hợp lệ.',PREVIEW_TARGET_NOT_CONFIGURED:'Chưa cấu hình thư mục preview tạm an toàn.',PREVIEW_TARGET_UNSAFE:'Đích preview nằm ngoài vùng tạm an toàn.',PUBLIC_RELEASE_ACTIVE_OTHER_REVIEW:'Đã có release hiện hành ở review khác.',PREVIEW_WITHDRAWAL_FAILED:'Không thu hồi được artifact preview cũ.'};
window.loadReview=async function(id){await priorLoadReview(id);const detail=document.querySelector('#review-detail');if(!detail)return;const reviewResponse=await fetch('/api/reviews/'+encodeURIComponent(id),{credentials:'same-origin'});const review=await reviewResponse.json();if(!reviewResponse.ok)return;const panel=document.createElement('section');panel.id='public-release';panel.className='item';panel.innerHTML='<h4>Duyệt công bố riêng trên Website</h4><p class="muted">Quyết định riêng với Gate A. Staging sẽ công khai trên Customer staging; private test chỉ tạo preview. Production chỉ đổi bằng Owner-triggered deploy.</p><p data-release-status>Đang kiểm tra...</p><p data-release-lineage class="muted"></p><p data-release-facts class="muted"></p><p data-release-media class="muted"></p><div class="item"><h5>Duyệt quyền ảnh cho Website</h5><p data-media-rights-summary class="muted">Ảnh chỉ được sao chép sang public-assets staging sau khi Owner xác nhận quyền.</p><select data-media-appearance aria-label="Nguồn ảnh"></select><select data-media-rights-status aria-label="Loại quyền"><option value="PERSONAL">Ảnh cá nhân</option><option value="COMPANY">Ảnh công ty</option></select><button type="button" data-media-rights-approve class="secondary" style="min-height:44px">Duyệt quyền ảnh Web</button><p data-media-rights-message class="muted" role="status"></p></div><details data-confirm-preview><summary>Xem dữ liệu sẽ public</summary><div data-confirm-preview-body></div></details><ul data-release-blockers class="err"></ul><div class="row"><button type="button" data-release-approve style="min-height:44px;flex:1 1 180px">XÁC NHẬN ĐĂNG</button><button type="button" data-release-revoke class="secondary" style="min-height:44px;flex:1 1 180px">Thu hồi công bố</button></div><p data-release-message class="muted" role="status"></p>';
detail.append(panel);const statusNode=panel.querySelector('[data-release-status]'),lineage=panel.querySelector('[data-release-lineage]'),factsNode=panel.querySelector('[data-release-facts]'),mediaNode=panel.querySelector('[data-release-media]'),blockerList=panel.querySelector('[data-release-blockers]'),previewBody=panel.querySelector('[data-confirm-preview-body]'),message=panel.querySelector('[data-release-message]'),approve=panel.querySelector('[data-release-approve]'),revoke=panel.querySelector('[data-release-revoke]'),appearanceSelect=panel.querySelector('[data-media-appearance]'),rightsSelect=panel.querySelector('[data-media-rights-status]'),rightsButton=panel.querySelector('[data-media-rights-approve]'),rightsSummary=panel.querySelector('[data-media-rights-summary]'),rightsMessage=panel.querySelector('[data-media-rights-message]');let current=null;
const refreshRights=async()=>{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/media-rights',{credentials:'same-origin'});const data=await response.json();if(!response.ok){rightsSummary.textContent='Không tải được trạng thái quyền ảnh.';rightsButton.disabled=true;return}const rows=data.appearances||[];appearanceSelect.replaceChildren(...rows.map(x=>{const option=document.createElement('option');option.value=x.appearance_id;option.textContent=x.appearance_id+' · '+x.media_count+' file · '+(x.approved?'đã duyệt '+x.rights_status:'chưa duyệt');return option}));rightsSummary.textContent=rows.some(x=>x.media_count>0)?'Chọn nguồn ảnh và xác nhận PERSONAL hoặc COMPANY. Chỉ staging được cấp quyền.':'Chưa có ảnh thuộc intake này.';rightsButton.disabled=!rows.some(x=>x.media_count>0)};
const renderConfirmationPreview=data=>{previewBody.replaceChildren();const dto=data.confirmation_preview;if(!dto){const p=document.createElement('p');p.className='err';p.textContent='Preview chưa sẵn sàng; xem blockers trước khi xác nhận.';previewBody.append(p);return}const fields=[['Tiêu đề',dto.title],['Loại BĐS',dto.propertyType],['Giá',dto.priceLabel||dto.price],['Diện tích',dto.area],['Địa chỉ/vị trí',dto.addressText],['Pháp lý',dto.legal||'UNKNOWN'],['Trạng thái',dto.status],['Mô tả sẽ public',dto.description],['Property / revision',dto.propertyId+' · r'+dto.propertyRevision],['Content',dto.contentVariantId?dto.contentVariantId+' · r'+dto.contentRevision:'Facts-only']];for(const [label,value] of fields){const row=document.createElement('p'),b=document.createElement('b'),span=document.createElement('span');b.textContent=label+': ';span.textContent=value==null||value===''?'UNKNOWN':String(value);row.append(b,span);previewBody.append(row)}const media=data.confirmation_media||[],gallery=document.createElement('div');gallery.className='intake-media-grid';for(const item of media){const img=document.createElement('img');img.src='/api/reviews/'+encodeURIComponent(id)+'/public-release/media/'+encodeURIComponent(item.media_id);img.alt='Ảnh đã được duyệt quyền public';img.loading='lazy';img.className='intake-media-thumb';gallery.append(img)}if(media.length)previewBody.append(gallery);else{const p=document.createElement('p');p.className='muted';p.textContent='Không có ảnh trong projection này.';previewBody.append(p)}const note=document.createElement('p');note.className='muted';note.textContent='Preview được dựng từ canonical Property Core và quyền ảnh hiện tại; publication ID chỉ được tạo khi Owner xác nhận.';previewBody.append(note)};
const confirmPublication=data=>new Promise(resolve=>{const dto=data.confirmation_preview,dialog=document.createElement('dialog');dialog.className='intake-preview';const heading=document.createElement('h2');heading.textContent=data.publication_mode==='STAGING_PUBLIC'?'Xác nhận đăng lên Customer Staging':'Xác nhận tạo preview private';dialog.append(heading);const lineage=document.createElement('p');lineage.textContent='Property '+dto.propertyId+' · revision '+dto.propertyRevision+(dto.contentVariantId?' · Content '+dto.contentVariantId+' r'+dto.contentRevision:' · facts-only');dialog.append(lineage);const grid=document.createElement('div');for(const [label,value] of [['Tiêu đề',dto.title],['Loại',dto.propertyType],['Giá',dto.priceLabel||dto.price],['Diện tích',dto.area],['Địa chỉ/vị trí',dto.addressText],['Pháp lý',dto.legal||'UNKNOWN'],['Mô tả',dto.description]]){const row=document.createElement('p'),b=document.createElement('b');b.textContent=label+': ';row.append(b,document.createTextNode(value==null||value===''?'UNKNOWN':String(value)));grid.append(row)}dialog.append(grid);const gallery=document.createElement('div');gallery.className='intake-media-grid';for(const item of(data.confirmation_media||[])){const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');img.src='/api/reviews/'+encodeURIComponent(id)+'/public-release/media/'+encodeURIComponent(item.media_id);img.alt='Ảnh đã được duyệt quyền public';img.className='intake-media-thumb';caption.textContent='Quyền sử dụng: '+item.rights_status;figure.append(img,caption);gallery.append(figure)}if(!gallery.childElementCount){const p=document.createElement('p');p.textContent='Không có ảnh trong projection.';gallery.append(p)}dialog.append(gallery);const rights=document.createElement('p');rights.textContent='Ảnh đủ quyền: '+(data.media_summary?.eligible||0)+' · bị chặn: '+(data.media_summary?.blocked||0)+' · Customer publish revision này sau khi xác nhận.';dialog.append(rights);const cancel=document.createElement('button');cancel.type='button';cancel.className='secondary';cancel.textContent='Hủy';const confirm=document.createElement('button');confirm.type='button';confirm.textContent=data.publication_mode==='STAGING_PUBLIC'?'XÁC NHẬN ĐĂNG':'XÁC NHẬN PREVIEW';const actions=document.createElement('div');actions.className='row';actions.append(cancel,confirm);dialog.append(actions);let accepted=false;dialog.addEventListener('close',()=>{dialog.remove();resolve(accepted)});cancel.onclick=()=>dialog.close();confirm.onclick=()=>{accepted=true;dialog.close()};document.body.append(dialog);dialog.showModal()});
const refresh=async()=>{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/public-release',{credentials:'same-origin'});const data=await response.json();current=data;await refreshRights();if(!response.ok){statusNode.textContent='Không tải được trạng thái công bố: '+(blockerLabels[data.error]||data.error||response.status);return}statusNode.textContent=data.state==='PUBLIC_ALLOWED'&&data.publication_mode==='STAGING_PUBLIC'?'Đang công khai trên Website staging':(stateLabels[data.state]||data.state);approve.textContent=data.publication_mode==='STAGING_PUBLIC'?'XEM VÀ XÁC NHẬN ĐĂNG':'XEM VÀ XÁC NHẬN PREVIEW';revoke.textContent=data.publication_mode==='STAGING_PUBLIC'?'Gỡ khỏi Website staging':'Thu hồi preview';lineage.textContent='Gate A '+(data.gate_a_state||'UNKNOWN')+' · review '+id+' r'+data.review_revision+' · property '+(data.property_id||'—')+' · Core r'+data.property_revision;renderConfirmationPreview(data);const f=data.canonical_facts||{};factsNode.textContent='Facts Core: '+[f.title,f.property_type,f.address,f.area,f.price,f.price_type,f.public_status,f.legal_status].map(x=>x==null?'—':String(x)).join(' · ');const m=data.media_summary||{};mediaNode.textContent='Media active: '+(m.active||0)+' · đủ quyền và asset public: '+(m.eligible||0)+' · bị chặn: '+(m.blocked||0);blockerList.replaceChildren(...(data.blockers||[]).map(code=>{const li=document.createElement('li');li.textContent=blockerLabels[code]||code;return li}));approve.disabled=!data.can_approve;revoke.disabled=!data.can_revoke};
const act=async action=>{if(!current)return;let reason='';if(action==='revoke'){reason=window.prompt('Lý do thu hồi quyền công bố?')||'';if(!reason.trim())return;if(!window.confirm('Xác nhận gỡ khỏi Website staging/preview?'))return}else{const fresh=await fetch('/api/reviews/'+encodeURIComponent(id)+'/public-release',{credentials:'same-origin',cache:'no-store'}),snapshot=await fresh.json();current=snapshot;if(!fresh.ok||!snapshot.can_approve||!snapshot.confirmation_preview){const labels=(snapshot.blockers||[]).map(code=>blockerLabels[code]||code);message.className='err';message.textContent=labels.join(' · ')||blockerLabels[snapshot.error]||snapshot.error||'PUBLIC_CONFIRMATION_PREVIEW_BLOCKED';await refresh();return}if(!await confirmPublication(snapshot))return}approve.disabled=true;revoke.disabled=true;message.className='muted';message.textContent='Đang xử lý...';const nonce=window.crypto?.randomUUID?window.crypto.randomUUID():Date.now()+'-'+Math.random().toString(16).slice(2),selected=current.selected_content||{};try{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/public-release/'+action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({property_id:current.property_id,expected_review_revision:current.review_revision,expected_property_revision:current.property_revision,expected_content_variant_id:selected.content_variant_id||null,expected_content_revision:selected.content_revision??null,idempotency_key:'admin-ui:'+action+':'+id+':'+nonce,reason})});const result=await response.json();if(!response.ok){const labels=(result.blockers||[]).map(code=>blockerLabels[code]||code);throw new Error(labels.join(' · ')||blockerLabels[result.error]||result.error||'HTTP '+response.status)}message.className='ok';message.textContent=action==='approve'?(result.published?'Đã công khai trên Customer Staging. Production chưa thay đổi.':'Preview private đã cập nhật, chưa public.'):(current.publication_mode==='STAGING_PUBLIC'?'Đã gỡ tin khỏi Customer Staging.':'Đã thu hồi preview.');await refresh()}catch(error){message.className='err';message.textContent=error.message;await refresh()}};
rightsButton.onclick=async()=>{if(!current||!appearanceSelect.value)return;const status=rightsSelect.value;if(!window.confirm('Owner xác nhận quyền '+(status==='PERSONAL'?'cá nhân':'công ty')+' cho toàn bộ ảnh trong nguồn này trên Website staging?'))return;rightsButton.disabled=true;rightsMessage.className='muted';rightsMessage.textContent='Đang kiểm tra file và ghi nhận quyền...';try{const response=await fetch('/api/reviews/'+encodeURIComponent(id)+'/media-rights',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({property_id:current.property_id,expected_property_revision:current.property_revision,appearance_id:appearanceSelect.value,rights_status:status})});const data=await response.json();if(!response.ok)throw Error(data.error||'HTTP '+response.status);rightsMessage.className='ok';rightsMessage.textContent='Đã xác nhận '+data.media_count+' ảnh; bài vẫn chưa publish.';await refresh()}catch(error){rightsMessage.className='err';rightsMessage.textContent=error.message;await refresh()}};approve.onclick=()=>act('approve');revoke.onclick=()=>act('revoke');await refresh()};
})();
</script>'''.encode('utf-8')

CONTENT_STUDIO_UI = '''<script>
(()=> {
  const priorLoadReview = window.loadReview;
  window.loadReview = async function(id) {
    await priorLoadReview(id);
    const host=document.querySelector('#review-detail');
    if(!host)return;
    host.querySelector('#content-studio')?.remove();host.querySelector('#review-evidence')?.remove();
    const response=await fetch('/api/reviews/'+encodeURIComponent(id),{credentials:'same-origin',cache:'no-store'});
    const pkg=await response.json();if(!response.ok)return;
    const panel=document.createElement('section');panel.className='item';panel.id='content-studio';
    panel.innerHTML='<h4>Content Studio · riêng tư</h4><p class="muted">Mỗi lần Tạo Content tạo đúng 5 bản trong một batch. Facts, nguồn và revision được liên kết; ảnh chưa gửi cho AI. Gate A, Gate B và đăng Website là các quyết định riêng.</p><p data-message role="status"></p><div data-variants></div>';
    host.append(panel);const message=panel.querySelector('[data-message]'),cards=panel.querySelector('[data-variants]');
    const evidencePanel=document.createElement('div');evidencePanel.className='item';evidencePanel.id='review-evidence';
    const evidenceResponse=await fetch('/api/reviews/'+encodeURIComponent(id)+'/evidence',{credentials:'same-origin',cache:'no-store'});
    const evidenceState=await evidenceResponse.json();
    if(evidenceState.status!=='PASS'){
      evidencePanel.innerHTML='<h5>Thông tin nguồn và quyền</h5><p class="muted">Chỉ Owner nhập citation và xác nhận quyền đúng theo nguồn. Thiếu thông tin này thì Content bị chặn; ảnh không gửi cho AI.</p><label>Mã/tên nguồn</label><input data-source-id maxlength="160"><label>Thông tin nguồn xác nhận</label><textarea data-claim maxlength="1000"></textarea><label>Quyền của nguồn</label><select data-citation-rights><option value="">Chọn</option><option>PERSONAL</option><option>COMPANY</option><option>APPROVED</option></select><label>Quyền sử dụng tổng thể</label><select data-rights><option value="">Chọn</option><option>PERSONAL</option><option>COMPANY</option><option>APPROVED</option></select><button type="button" data-save-evidence>Lưu xác nhận nguồn</button><p data-evidence-status role="status"></p>';
      evidencePanel.querySelector('[data-save-evidence]').onclick=async()=>{const source_id=evidencePanel.querySelector('[data-source-id]').value.trim(),claim=evidencePanel.querySelector('[data-claim]').value.trim(),citation_rights=evidencePanel.querySelector('[data-citation-rights]').value,rights_status=evidencePanel.querySelector('[data-rights]').value,status=evidencePanel.querySelector('[data-evidence-status]');if(!source_id||!claim||!citation_rights||!rights_status){status.className='err';status.textContent='Cần Owner điền đủ citation và xác nhận quyền.';return}status.textContent='Đang lưu…';const r=await fetch('/api/reviews/'+encodeURIComponent(id)+'/evidence',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({citations:[{source_id,claim,rights_status:citation_rights}],rights_status})});const d=await r.json();status.className=r.ok?'ok':'err';status.textContent=r.ok?'Đã lưu '+d.evidence_revision_id+'; có thể bấm TẠO CONTENT.':(d.error||'EVIDENCE_SAVE_FAILED');if(r.ok)await window.loadReview(id)};
      panel.prepend(evidencePanel);
    }else{const status=document.createElement('p');status.className='muted';status.textContent='Nguồn/quyền đã có xác nhận revision '+evidenceState.evidence_revision_id+'.';evidencePanel.append(status);panel.prepend(evidencePanel)}
    const post=async(action,data)=>{const r=await fetch('/api/reviews/'+encodeURIComponent(id)+'/content/'+action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error||'HTTP '+r.status);return d};
    for(const batch of(pkg.content_batches||[])){
      const head=document.createElement('p');head.className='muted';const g=batch.generation||{},u=g.usage||{};
      head.textContent=`${batch.status||'PRIVATE_DRAFT'} · ${batch.content_batch_id} · facts ${batch.facts_revision_id} · ${g.model||''} · ${u.input_tokens??'—'}/${u.output_tokens??'—'} tokens · ${g.request_id||'—'} · est. ${g.estimated_cost_usd??'—'} USD · media ${batch.media_analysis||'NOT_SENT'}`;cards.append(head);
      if((batch.normalized_facts||[]).length){const f=document.createElement('p');f.className='muted';f.textContent='Facts trích từ nguồn (chưa Owner xác nhận): '+batch.normalized_facts.map(x=>x.field+'='+x.normalized_value+' ['+x.evidence+']').join(' · ');cards.append(f)}
      for(const variant of(batch.variants||[])){
        const card=document.createElement('article');card.className='item';card.innerHTML='<b></b><label>Tiêu đề</label><input data-title><label>Nội dung</label><textarea data-body rows="5"></textarea><button type="button" data-edit>Lưu chỉnh sửa</button><button type="button" data-select class="secondary">Chọn mẫu</button><div data-channels></div><p data-status class="muted"></p>';
        card.querySelector('b').textContent=(variant.status||'DRAFT')+' · '+variant.content_variant_id+' · revision '+variant.content_revision;
        card.querySelector('[data-title]').value=variant.title||'';card.querySelector('[data-body]').value=variant.body||'';
        const base={content_variant_id:variant.content_variant_id,expected_content_revision:variant.content_revision};
        const run=async(action,data)=>{try{message.textContent='Đang lưu…';await post(action,{...base,...data});await window.loadReview(id)}catch(e){message.className='err';message.textContent=e.message||'Thao tác thất bại.'}};
        card.querySelector('[data-edit]').onclick=()=>run('edit',{title:card.querySelector('[data-title]').value,body:card.querySelector('[data-body]').value});
        card.querySelector('[data-select]').onclick=()=>run('select',{});
        const channels=card.querySelector('[data-channels]');
        for(const[channel,label]of[['public_web','Duyệt cho Website'],['facebook','Duyệt bản nháp Facebook'],['zalo_company','Duyệt bản nháp Zalo'],['tiktok','Duyệt bản nháp TikTok'],['instagram','Duyệt bản nháp Instagram'],['youtube','Duyệt bản nháp YouTube'],['x','Duyệt bản nháp X'],['linkedin','Duyệt bản nháp LinkedIn']]){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;b.onclick=()=>run('approve-channel',{channel});channels.append(b)}const channelPreviews=variant.channel_previews||[];if(channelPreviews.length){const drafts=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Preview channel adapters · không kết nối/không đăng';drafts.append(summary);for(const draft of channelPreviews){const row=document.createElement('article');row.className='item';const title=document.createElement('b');title.textContent=draft.label+' · '+draft.approval_status;const caption=document.createElement('p');caption.textContent=draft.caption;const meta=document.createElement('p');meta.className='muted';meta.textContent='ID '+draft.channel_draft_id+' · '+draft.connection_status+' / '+draft.dispatch_status+' · format '+draft.format_metadata.profile+' · media '+draft.media_status+' · r'+draft.content_revision;row.append(title,caption,meta);drafts.append(row)}card.append(drafts)}
        card.querySelector('[data-status]').textContent=(variant.channel_approvals||[]).map(x=>x.channel+': r'+x.content_revision+' '+x.status).join(' · ')||'Chưa duyệt kênh; không có kênh nào tự đăng.';cards.append(card);
      }
    }
    if(!(pkg.content_batches||[]).length)message.textContent='Chưa có Content batch. Về Intake và bấm TẠO CONTENT.';
  };
})();
</script>'''.encode("utf-8")


def page() -> bytes:
    return """<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SALA BĐS · First Web Admin</title><style>
:root{font-family:system-ui,sans-serif;color:#15243a;background:#f8fafc}*{box-sizing:border-box}body{margin:0}header{background:#0f2d5b;color:#fff;padding:18px 20px}main{max-width:900px;margin:auto;padding:18px 16px 60px}.grid{display:grid;gap:16px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 8px 20px #0f2d5b0c}.kicker{font-size:.72rem;letter-spacing:.12em;font-weight:800;color:#2563eb}h1,h2{margin:0 0 10px}label{display:block;font-weight:700;font-size:.9rem;margin:10px 0 5px}input,textarea,select{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}textarea{min-height:90px}button{border:0;border-radius:10px;padding:11px 15px;background:#0f2d5b;color:#fff;font:inherit;font-weight:800;cursor:pointer}.secondary{background:#e8effd;color:#2563eb}.row{display:flex;gap:8px;flex-wrap:wrap}.muted{color:#64748b;font-size:.9rem}.status{display:inline-block;border-radius:999px;padding:4px 9px;background:#e8effd;color:#2563eb;font-size:.75rem;font-weight:800}.item{border-top:1px solid #e5e7eb;padding-top:12px;margin-top:12px}.result{padding:10px 0;border-bottom:1px solid #e5e7eb}.ok{color:#07845e}.err{color:#b91c1c;white-space:pre-wrap}.owner-nav{position:sticky;top:0;z-index:10;display:flex;gap:8px;overflow-x:auto;padding:10px 12px;background:#fff;border-bottom:1px solid #dbe3ee}.owner-nav a{flex:0 0 auto;padding:9px 12px;border-radius:999px;background:#e8effd;color:#0f2d5b;font-weight:750;text-decoration:none;min-height:42px;display:flex;align-items:center}.owner-nav a:focus-visible{outline:3px solid #2563eb;outline-offset:2px}.intake-media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:10px;margin:10px 0}.intake-media-card{min-width:0;border:1px solid #dbe3ee;border-radius:12px;padding:8px;background:#fff}.intake-media-thumb{display:block;width:100%;height:112px;object-fit:cover;border-radius:8px;background:#f1f5f9}.intake-media-open{width:100%;padding:0;background:none}.intake-media-name{display:block;overflow-wrap:anywhere;font-size:.82rem;margin:6px 0}.intake-media-card button.remove-media{width:100%;min-height:42px;background:#fff1f2;color:#9f1239}.intake-media-duplicate{display:block;color:#a16207;font-size:.76rem;margin:4px 0}.intake-preview{max-width:min(94vw,900px);max-height:90vh;border:0;border-radius:14px;padding:12px}.intake-preview img{display:block;max-width:88vw;max-height:78vh;object-fit:contain}.intake-preview::backdrop{background:#0f172acc}@media(min-width:700px){.grid{grid-template-columns:1fr 1fr}.owner-nav{justify-content:center}}
</style></head><body><header id="owner-home"><strong>SALA - BĐS</strong><div>First Web · Admin Staging</div></header><nav class="owner-nav" aria-label="Điều hướng Admin"><a href="#home-dashboard">Home</a><a href="#intake-section">Nhập nguồn</a><a href="#review-section">Content / xác nhận dữ kiện</a><a href="/website">Quản lý Website</a></nav><main><section id="intake-section" class="card"><p class="kicker">INTAKE</p><h1>Nhận nguồn mới</h1><p class="muted">Bản gốc lưu ngoài public root. Intake vào Property Core sau xử lý và Gate A; Website publish là quyết định riêng.</p><form id="intake" enctype="multipart/form-data"><label>Nguồn</label><select name="source"><option>Cá nhân</option><option>Zalo công ty</option><option>Khác</option></select><label>Nhóm/nguồn chi tiết</label><input name="sourceGroup" placeholder="Tên nhóm hoặc người cung cấp (nếu có)"><label>Thông tin sơ bộ</label><textarea name="note" placeholder="Giá, khu vực, pháp lý... chỉ ghi điều nguồn cung cấp"></textarea><label>Mã nguồn trích dẫn</label><input name="evidence_source_id" maxlength="160" placeholder="Ví dụ: chủ nhà cung cấp ngày..." autocomplete="off"><label>Thông tin nguồn xác nhận</label><textarea name="evidence_claim" maxlength="1200" placeholder="Ghi ngắn gọn thông tin nguồn thực sự xác nhận; không tự suy diễn"></textarea><label>Quyền sử dụng nguồn cho việc tạo Content</label><select name="evidence_rights"><option value="">Chọn sau khi xác minh quyền</option><option value="PERSONAL">Cá nhân</option><option value="COMPANY">Công ty</option><option value="APPROVED">Đã có chấp thuận</option></select><p class="muted">Ảnh gốc không được gửi cho AI. Chỉ nhấn TẠO CONTENT sau khi xác minh nguồn và quyền.</p><label>Ảnh, video, ghi âm hoặc file gốc</label><input id="intake-media-input" name="media" type="file" multiple accept="image/*,video/*,audio/*,.txt,.json,.md" hidden><button id="intake-add-media" type="button" class="secondary" aria-controls="intake-media-input">Thêm ảnh/tệp</button><p id="intake-media-count" class="muted" aria-live="polite">0 ảnh/tệp đã chọn</p><div id="intake-media-list" class="intake-media-grid"></div><dialog id="intake-preview" class="intake-preview"><button id="intake-preview-close" type="button" class="secondary">Đóng ảnh</button><img id="intake-preview-image" alt="Ảnh xem trước"></dialog><button id="intake-save" type="submit">TẠO CONTENT</button><button id="intake-new" type="button" class="secondary" hidden>Bắt đầu Intake mới (nguồn hiện tại đã lưu)</button></form><p id="intake-msg" role="status"></p></section><section id="property-search" class="card"><p class="kicker">PROPERTY CORE</p><h2>Tra cứu listing thật</h2><form id="search"><input name="q" placeholder="Ví dụ: Thôn 8 sổ riêng 2 tỷ"><br><br><button class="secondary" type="submit">Tìm trong Property Core</button></form><div id="results"></div></section><section id="review-section" class="card"><p class="kicker">REVIEW CENTER · PRIVATE</p><h2>Hàng chờ duyệt Gate A</h2><p class="muted">Phân tích nội bộ chỉ dành cho Admin. Sửa thông tin quan trọng sẽ làm review thành STALE_REVIEW và phải tạo lại phân tích.</p><div id="reviews" class="muted">Đang tải...</div><div id="review-detail"></div></section><section class="card"><p class="kicker">QUEUE</p><h2>Approval Queue</h2><div id="queue" class="muted">Đang tải...</div></section></main><script>
const $=s=>document.querySelector(s);const mojibake={'C�� nh��n':'Cá nhân','C�� nh��n':'Cá nhân','Zalo c��ng ty':'Zalo công ty','Kh��c':'Khác'};const displayText=s=>{const v=String(s??'');return mojibake[v]||(/[�]/.test(v)?'Nội dung tiếng Việt đã lưu':v);};const esc=s=>String(displayText(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function loadReviews(){const r=await fetch('/api/reviews');const d=await r.json();if(!r.ok){$('#reviews').textContent=d.error||'Không tải được review';return}$('#reviews').innerHTML=d.length?d.map(x=>`<div class="item"><button class="secondary" data-review="${esc(x.review_id)}">Mở</button> <b>${esc(x.state)}</b> · ${esc(x.source||'')} · ${esc(x.intake_id||'')} · revision ${x.review_revision}</div>`).join(''):'Chưa có review.';document.querySelectorAll('[data-review]').forEach(b=>b.onclick=()=>loadReview(b.dataset.review))}
async function loadReview(id){const r=await fetch('/api/reviews/'+encodeURIComponent(id));const x=await r.json();if(!r.ok){$('#review-detail').textContent=x.error;return}const ia=x.internal_analysis||{},ds=ia.deal_score||{};$('#review-detail').innerHTML=`<div class="item"><h3>${esc(x.state)} · revision ${x.review_revision}</h3><h4>Nguồn / provenance</h4><pre>${esc(JSON.stringify(x.candidate?.raw||x.candidate?.provenance||{},null,2))}</pre><h4>Property facts</h4><pre>${esc(JSON.stringify(x.candidate?.facts||{},null,2))}</pre><h4>Thiếu / chưa Owner xác nhận</h4><pre>${esc(JSON.stringify(x.candidate?.missing_fields||[],null,2))}</pre><h4>Market / Comparable / Valuation</h4><pre>${esc(JSON.stringify({snapshot:x.market_snapshot?.market_snapshot_id,comparables:x.comparable_set?.status,valuation:x.valuation?.valuation_summary||x.valuation},null,2))}</pre><h4>AI Internal Analysis · PRIVATE</h4><pre>${esc(JSON.stringify({score:ds, strengths:ia.strengths, risks:ia.weaknesses_risks, anomalies:ia.anomalies, missing:ia.missing_information, questions:ia.questions_to_verify, recommendation:ia.recommended_action, confidence:ia.confidence},null,2))}</pre><p class="err">${x.state==='STALE_REVIEW'?'Phân tích đã cũ; không thể duyệt.':''}</p><label>Sửa field</label><input id="review-field" placeholder="ví dụ transaction_type, price_vnd_per_month, house_type, bedrooms"><input id="review-value" placeholder="Giá trị mới"><button data-edit-review>SAVE REVIEW REVISION</button> <button data-approve-review ${x.state==='STALE_REVIEW'||x.state==='REJECTED'?'disabled':''}>DUYỆT GATE A</button> <button data-reject-review>BỎ / TỪ CHỐI</button><p id="review-msg"></p></div>`;document.querySelector('[data-edit-review]').onclick=()=>reviewAction(id,'edit',x.review_revision,{field:$('#review-field').value,value:$('#review-value').value,owner_id:'owner'});document.querySelector('[data-approve-review]').onclick=()=>reviewAction(id,'approve',x.review_revision,{decision:'CREATE_NEW_PROPERTY',idempotency_key:'ui:'+id+':'+x.review_revision,owner_id:'owner'});document.querySelector('[data-reject-review]').onclick=()=>reviewAction(id,'reject',x.review_revision,{reason:'Owner rejected',owner_id:'owner'})}
async function reviewAction(id,kind,revision,extra){const r=await fetch('/api/reviews/'+id+'/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:revision,...extra})});const d=await r.json();const gateErrors={MISSING_FIELDS_REQUIRE_OWNER:'Cần Owner bổ sung/xác nhận trường bắt buộc theo loại BĐS.',PROPERTY_TYPE_UNSUPPORTED:'Loại BĐS chưa có quy tắc Gate A.',RENT_PERIOD_NOT_SUPPORTED_BY_PROPERTY_CORE:'Property Core hiện chỉ lưu được giá thuê theo tháng; kỳ hạn này chưa thể duyệt.',SALE_PRICE_PERIOD_INVALID:'Giá bán không nhận chu kỳ thuê.'};$('#review-msg').textContent=r.ok?'Đã cập nhật: '+(d.state||'OK'):(gateErrors[d.error]||d.error||'Lỗi');loadReviews();if(r.ok)loadReview(id)}
async function load(){const r=await fetch('/api/intakes',{credentials:'same-origin',cache:'no-store'});const rows=await r.json();if(!r.ok){$('#queue').textContent=rows.error||'Không tải được Intake';return}window.intakeRecords=rows;const host=$('#queue');host.replaceChildren();const isTestOnly=x=>x.source==='TEST_ONLY'||[x.sourceGroup,x.note,x.clientBatchId].some(v=>String(v||'').includes('TEST_ONLY'));const hidden=rows.filter(isTestOnly).length;const visible=rows.filter(x=>!isTestOnly(x));if(hidden){const note=document.createElement('p');note.className='muted';note.textContent=hidden+' intake TEST_ONLY được ẩn khỏi hàng đợi vận hành; dữ liệu chưa bị xóa.';host.append(note)}if(!visible.length){const empty=document.createElement('p');empty.textContent='Chưa có intake cần xử lý.';host.append(empty);return}for(const x of visible){const row=document.createElement('article');row.className='item';const title=document.createElement('b');title.textContent=x.id;const state=document.createElement('span');state.className='status';state.textContent=x.status||'RECEIVED';const detail=document.createElement('div');detail.textContent=(x.source||'')+' · '+(x.createdAt||'');const note=document.createElement('div');note.className='muted';note.textContent=(x.media||[]).length+' media · '+(x.note||'Không có ghi chú');row.append(title,document.createTextNode(' '),state,detail,note);if(['RECEIVED','DRAFT_NEEDS_REVIEW'].includes(x.status||'RECEIVED')){const action=document.createElement('button');action.type='button';action.textContent='TIẾP TỤC XỬ LÝ';action.onclick=()=>createContent(x.id,action);row.append(action)}else if(x.status==='CONTENT_CREATED'&&x.contentReviewId){const action=document.createElement('button');action.type='button';action.textContent='XEM 5 CONTENT';action.onclick=async()=>{await loadReviews();await window.loadReview(x.contentReviewId);document.querySelector('#review-section').scrollIntoView({behavior:'smooth'})};row.append(action)}const msg=document.createElement('p');msg.setAttribute('data-intake-action-status','');msg.setAttribute('role','status');row.append(msg);host.append(row)}}
const intakeForm=$('#intake'),mediaInput=$('#intake-media-input'),addMedia=$('#intake-add-media'),mediaCount=$('#intake-media-count'),mediaList=$('#intake-media-list'),previewDialog=$('#intake-preview'),previewImage=$('#intake-preview-image'),previewClose=$('#intake-preview-close'),saveIntake=$('#intake-save'),newIntake=$('#intake-new');let intakeFiles=[],intakeBusy=false,intakeScanGeneration=0;let pendingSavedIntakeId=null;newIntake.onclick=()=>{if(!pendingSavedIntakeId)return;intakeForm.reset();pendingSavedIntakeId=null;newIntake.hidden=true;intakeFiles.forEach(x=>{if(x.url)URL.revokeObjectURL(x.url)});intakeFiles=[];renderIntakeFiles();$('#intake-msg').textContent='Nguồn Intake trước đã lưu trong Queue.'};async function createContent(intakeId,button){if(button?.disabled)return;if(button)button.disabled=true;const statusNode=button?.closest('article')?.querySelector('[data-intake-action-status]')||$('#intake-msg');statusNode.className='muted';statusNode.textContent='Đang kiểm tra đúng Intake và quyền nguồn…';const keyName='sala-content-idem:'+intakeId;let key=sessionStorage.getItem(keyName);if(!key){key=crypto.randomUUID().replace(/-/g,'');sessionStorage.setItem(keyName,key)}try{const r=await fetch('/api/intakes/'+encodeURIComponent(intakeId)+'/create-content',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({idempotency_key:key})});let d;try{d=await r.json()}catch(_){throw Error('Máy chủ trả phản hồi không hợp lệ; giữ khóa chống lặp để không gửi lại tự động.')}if(r.status!==201&&r.status!==200){if(r.status<500&&d.error!=='CONTENT_REQUEST_OUTCOME_UNKNOWN')sessionStorage.removeItem(keyName);if(d.error==='CONTENT_PREREQUISITES_REQUIRED'&&window.openMissingFields){await load();const record=(window.intakeRecords||[]).find(x=>x.id===intakeId);window.openMissingFields(intakeId,d,record)}else{statusNode.className='err';statusNode.textContent=(d.error||'CONTENT_CREATE_FAILED')+(d.provider_calls===0?' · Chưa gọi AI.':'')+' · Intake đã lưu: '+intakeId+'.';await load();if(d.review_id){await loadReviews();await window.loadReview(d.review_id)}}return false}sessionStorage.removeItem(keyName);statusNode.className='ok';statusNode.textContent='Đã tạo 5 Content riêng tư · '+d.review_id+' · '+d.content_batch_id+' · chưa duyệt, chưa đăng.';await load();await loadReviews();await window.loadReview(d.review_id);return true}catch(e){statusNode.className='err';statusNode.textContent=(e.message||'Không rõ kết quả')+' · Intake đã lưu: '+intakeId+'. Khóa chống lặp được giữ; hãy kiểm tra Queue trước khi thử lại.';await load();return false}finally{if(button)button.disabled=false}}
const isImageFile=file=>file.type.startsWith('image/')||/\\.(jpe?g|png|webp|heic)$/i.test(file.name);
function renderIntakeFiles(){mediaCount.textContent=intakeFiles.length===0?'0 ảnh/tệp đã chọn':intakeFiles.length===1?'1 ảnh/tệp đã chọn':intakeFiles.length+' ảnh/tệp đã chọn';mediaList.replaceChildren();intakeFiles.forEach((item,index)=>{const card=document.createElement('div');card.className='intake-media-card';if(item.url){const open=document.createElement('button');open.type='button';open.className='intake-media-open';open.setAttribute('aria-label','Phóng to '+item.file.name);const img=document.createElement('img');img.className='intake-media-thumb';img.src=item.url;img.alt=item.file.name;open.append(img);open.onclick=()=>{previewImage.src=item.url;previewImage.alt=item.file.name;if(previewDialog.showModal)previewDialog.showModal();else window.open(item.url,'_blank','noopener')};card.append(open)}else{const icon=document.createElement('div');icon.className='intake-media-thumb';icon.textContent='TỆP';icon.setAttribute('aria-hidden','true');icon.style.display='grid';icon.style.placeItems='center';card.append(icon)}const name=document.createElement('span');name.className='intake-media-name';name.textContent=item.file.name+' · '+Math.ceil(item.file.size/1024)+' KB';card.append(name);if(item.duplicate){const badge=document.createElement('span');badge.className='intake-media-duplicate';badge.textContent='Ảnh trùng bytes · máy chủ sẽ giữ bản đầu';card.append(badge)}const remove=document.createElement('button');remove.type='button';remove.className='remove-media';remove.textContent='Bỏ ảnh/tệp';remove.disabled=intakeBusy;remove.onclick=()=>{if(item.url)URL.revokeObjectURL(item.url);intakeFiles.splice(index,1);scanIntakeDuplicates()};card.append(remove);mediaList.append(card)})}
async function scanIntakeDuplicates(){const generation=++intakeScanGeneration;intakeBusy=true;mediaInput.disabled=true;addMedia.disabled=true;renderIntakeFiles();const seen=new Set();let unsupported=false;for(const item of intakeFiles){item.duplicate=false;if(!isImageFile(item.file))continue;if(!window.crypto||!window.crypto.subtle){unsupported=true;continue}try{const bytes=await item.file.arrayBuffer(),digest=Array.from(new Uint8Array(await window.crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');item.duplicate=seen.has(digest);seen.add(digest)}catch(_){unsupported=true}}if(generation!==intakeScanGeneration)return;intakeBusy=false;mediaInput.disabled=false;addMedia.disabled=false;renderIntakeFiles();const duplicates=intakeFiles.filter(x=>x.duplicate).length;mediaCount.textContent=(intakeFiles.length===0?'0 ảnh/tệp đã chọn':intakeFiles.length+' ảnh/tệp đã chọn')+(duplicates?' · phát hiện '+duplicates+' ảnh trùng, bản đầu được giữ':'')+(unsupported?' · máy chủ sẽ kiểm tra trùng bytes':'')}
addMedia.addEventListener('click',()=>{if(!intakeBusy)mediaInput.click()});mediaInput.addEventListener('change',()=>{const added=Array.from(mediaInput.files||[]);mediaInput.value='';for(const file of added)intakeFiles.push({file,url:isImageFile(file)?URL.createObjectURL(file):null,duplicate:false});scanIntakeDuplicates()});previewClose.addEventListener('click',()=>previewDialog.close());previewDialog.addEventListener('click',e=>{if(e.target===previewDialog)previewDialog.close()});window.addEventListener('pagehide',()=>intakeFiles.forEach(x=>{if(x.url)URL.revokeObjectURL(x.url)}));
intakeForm.addEventListener('submit',async e=>{e.preventDefault();if(intakeBusy)return;intakeBusy=true;saveIntake.disabled=true;mediaInput.disabled=true;addMedia.disabled=true;const m=$('#intake-msg');m.textContent='Đang lưu Intake…';m.className='muted';try{if(!pendingSavedIntakeId){const data=new FormData(intakeForm);data.delete('media');intakeFiles.forEach(x=>data.append('media',x.file,x.file.name));const r=await fetch('/api/intakes',{method:'POST',body:data,credentials:'same-origin'});let d;try{d=await r.json()}catch(_){throw Error('Máy chủ trả về phản hồi không hợp lệ.')}if(r.status!==201&&r.status!==200)throw Error(d.error||d.code||`INTAKE_FAILED_HTTP_${r.status}`);pendingSavedIntakeId=d.id;const u=d.upload||{received:intakeFiles.length,accepted:d.media?.length||0,duplicate_count:0,duplicates:[]};m.textContent='Đã lưu '+d.id+' · '+u.accepted+'/'+u.received+' tệp được giữ. Đang tạo Content…'+(u.duplicate_count?' Đã bỏ '+u.duplicate_count+' tệp trùng.':'');await load()}const completed=await createContent(pendingSavedIntakeId,saveIntake);if(completed){intakeForm.reset();pendingSavedIntakeId=null;intakeFiles.forEach(x=>{if(x.url)URL.revokeObjectURL(x.url)});intakeFiles=[];renderIntakeFiles()}}catch(x){m.className='err';m.textContent=(x.message||'Không thể lưu Intake.')+(pendingSavedIntakeId?' · Intake đã lưu: '+pendingSavedIntakeId+'. Mở Queue để tiếp tục; không lưu lần hai.':'')}finally{newIntake.hidden=!pendingSavedIntakeId;intakeBusy=false;saveIntake.disabled=false;mediaInput.disabled=false;addMedia.disabled=false;renderIntakeFiles()}});
$('#search').onsubmit=async e=>{e.preventDefault();let q=new FormData(e.target).get('q');let r=await fetch('/api/search?q='+encodeURIComponent(q));let d=await r.json();$('#results').innerHTML=d.length?d.map(x=>`<div class="result"><b>${esc(x.title)}</b><br>${esc(x.slug)} · ${esc(x.source||'Chưa có nguồn')}<br>${esc(x.location||x.addressText||'')}</div>`).join(''):'Không có listing khớp dữ liệu thật.'};load();loadReviews();const requestedReview=new URLSearchParams(location.search).get('review');if(requestedReview)setTimeout(()=>loadReview(requestedReview),0);
</script></body></html>""".replace("out.textContent+='" + chr(10) + "CLEANUP:", "out.textContent+='\\\\nCLEANUP:").replace("AI9 TEST ONLY", "BỘ XỬ LÝ CONTENT · TEST_ONLY").replace("Nguồn / provenance", "Nguồn & quyền sử dụng").encode("utf-8")


ANALYTICS_CARD = '''<section id="owner-analytics" class="card" style="margin-top:16px" aria-labelledby="analytics-title"><p class="kicker">WEBSITE ACTIVITY · OWNER ONLY</p><h2 id="analytics-title">Hoạt động Website</h2><p class="muted">Chỉ là lượt sự kiện đã nhận; không suy ra số khách, cuộc gọi thành công hay lead. Không thu số điện thoại người truy cập.</p><div class="grid"><div><label for="analytics-from">Từ ngày</label><input id="analytics-from" type="date"></div><div><label for="analytics-to">Đến ngày</label><input id="analytics-to" type="date"></div><div><label for="analytics-property">Property ID (không bắt buộc)</label><input id="analytics-property" inputmode="text" maxlength="73" placeholder="property-…"></div><div><label for="analytics-type">Loại sự kiện</label><select id="analytics-type"><option value="">Tất cả</option><option value="page_view">page_view</option><option value="property_card_view">property_card_view</option><option value="property_detail_view">property_detail_view</option><option value="search_filter">search_filter</option><option value="contact_cta_click">contact_cta_click</option></select></div><div><label for="analytics-channel">Kênh CTA</label><select id="analytics-channel"><option value="">Tất cả</option><option value="call">call</option><option value="zalo">zalo</option><option value="other">other</option></select></div></div><br><button type="button" id="analytics-load">Tải báo cáo</button><p id="analytics-status" class="muted" role="status">Chưa tải báo cáo.</p><div id="analytics-results" aria-live="polite"></div></section>'''.encode("utf-8")

WEBSITE_MASTER_PAGE = '''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#102b4b"><title>SALA - BĐS | Quản lý Website</title><style>
:root{--navy:#102b4b;--navy2:#1a3b60;--ink:#182a3d;--muted:#6a7b8e;--line:#dfe6ee;--bg:#f4f7fa;--white:#fff;--ice:#eff4f9;--good:#22785d;--warn:#a4691a;--danger:#a54148}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:var(--ink)}a{color:inherit;text-decoration:none}button{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--navy);padding:9px 12px;font-weight:700}.app{display:grid;grid-template-columns:245px minmax(0,1fr);min-height:100vh}.side{background:linear-gradient(160deg,#0d2949,#163958);color:#e8f1f8;padding:22px 14px;position:sticky;top:0;height:100vh}.brand{padding:4px 10px 20px;border-bottom:1px solid #ffffff28;font-weight:800;font-size:16px}.brand small{display:block;font-size:10px;letter-spacing:1.4px;color:#a6c2dc;margin-top:3px}.nav{display:flex;flex-direction:column;gap:5px;margin-top:18px}.nav a{padding:10px 12px;border-radius:9px;color:#d4e4f3}.nav a:hover,.nav a:focus{background:#ffffff20;color:#fff}.sidefoot{position:absolute;bottom:18px;left:24px;color:#abc4d9;font-size:12px}.main{min-width:0}.top{background:#fff;position:sticky;top:0;padding:16px 26px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;z-index:4}.top h1{font-size:20px;margin:0}.env{font-size:11px;color:#315578;background:#eef5fc;border-radius:99px;padding:7px 11px;font-weight:800}.content{max-width:1480px;padding:22px 26px 60px;margin:auto}.notice{background:#eef4fa;border:1px solid #d8e5f0;border-radius:11px;padding:12px 14px;color:#52667b;margin-bottom:18px}.section{scroll-margin-top:85px;margin:18px 0}.section h2{font-size:19px;margin:0 0 5px}.muted{color:var(--muted);font-size:12px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card{background:#fff;border:1px solid #e0e8ef;border-radius:15px;padding:16px;box-shadow:0 8px 20px #0f2d5b0c;min-width:0}.metric{font-size:26px;font-weight:800;color:#172f4b}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px;background:#fff}table{border-collapse:collapse;width:100%;min-width:680px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--ice);font-size:11px;color:#52667b}td{font-size:12px}.status{display:inline-block;background:#eef4fa;color:#315578;border-radius:99px;padding:3px 8px;font-size:11px;font-weight:750}.empty{padding:15px;background:#f8fafc;border-radius:9px;color:var(--muted)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.primary{background:var(--navy);color:#fff;border-color:var(--navy)}.mobile-nav{display:none}.logo-word{font-weight:850;letter-spacing:.2px}.err{color:var(--danger)}@media(max-width:780px){.app{display:block}.side{display:none}.top{padding:13px 16px}.content{padding:15px 14px 86px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.mobile-nav{display:flex;position:fixed;z-index:10;bottom:0;left:0;right:0;overflow:auto;gap:6px;padding:8px 10px calc(8px + env(safe-area-inset-bottom));background:#fff;border-top:1px solid var(--line)}.mobile-nav a{flex:none;padding:9px 11px;border-radius:99px;background:var(--ice);color:var(--navy);font-weight:700;font-size:12px}.section{scroll-margin-top:72px}.tablewrap table{min-width:620px}}
</style></head><body><div class="app"><aside class="side"><div class="brand"><span class="logo-word">SALA - BĐS</span><small>QUẢN LÝ WEBSITE</small></div><nav class="nav"><a href="#overview">Tổng quan</a><a href="#listings">Tin BĐS & xuất bản</a><a href="#traffic">Traffic & hành vi</a><a href="#demand">Nhu cầu tìm kiếm</a><a href="#crm">CRM & chăm sóc</a><a href="#business">Hiệu quả kinh doanh</a><a href="#monitor">Giám sát & cảnh báo</a><a href="#integrations">Tích hợp & dữ liệu</a><a href="/">← Về SALA Admin</a></nav><div class="sidefoot">Đăng nhập Owner · cùng phiên SALA Admin</div></aside><div class="main"><header class="top"><h1>Quản lý Website</h1><span class="env">STAGING · KHÔNG PHẢI PRODUCTION</span></header><main class="content"><div class="notice"><b>Dữ liệu vận hành thật từ Admin.</b> Chỉ số không có nguồn hiển thị “Chưa có dữ liệu” hoặc “Chưa kết nối”. Lượt xem và lượt bấm không được diễn giải thành khách hay giao dịch. Nút đăng/gỡ dùng quy trình duyệt riêng tại Admin.</div><section id="overview" class="section"><h2>Tổng quan kinh doanh</h2><p class="muted">Ảnh chụp từ Property Core, Intake, Review và event store hiện có.</p><div class="grid" id="kpis"><div class="card">Đang tải nguồn…</div></div></section><section id="listings" class="section"><h2>Tin BĐS & xuất bản</h2><p class="muted">Chọn review để mở luồng duyệt Website hiện có. Gate A và Website publication là hai quyết định riêng.</p><div id="listing-table" class="empty">Đang tải…</div></section><section id="traffic" class="section"><h2>Traffic & hành vi</h2><div id="traffic-data" class="empty">Đang tải…</div></section><section id="demand" class="section"><h2>Nhu cầu tìm kiếm</h2><p class="muted">Chỉ truy vấn đã chuẩn hóa được ghi nhận; từ khóa thô và zero-result count hiện không được lưu.</p><div id="demand-data" class="empty">Đang tải…</div></section><section id="crm" class="section"><h2>CRM & chăm sóc</h2><div class="empty">Chưa kết nối CRM trung tâm. Event website không tạo hồ sơ khách hoặc lead.</div></section><section id="business" class="section"><h2>Hiệu quả kinh doanh</h2><div class="empty">Chưa có nguồn xác nhận lịch xem, giao dịch, doanh thu hoặc hoa hồng.</div></section><section id="monitor" class="section"><h2>Giám sát & cảnh báo</h2><div id="monitor-data" class="empty">Đang tải…</div></section><section id="integrations" class="section"><h2>Tích hợp & dữ liệu</h2><div id="integrations-data" class="empty">Đang tải…</div></section></main></div></div><nav class="mobile-nav" aria-label="Điều hướng nhanh"><a href="#overview">Tổng quan</a><a href="#listings">Sản phẩm</a><a href="#traffic">Traffic</a><a href="#crm">CRM</a><a href="#integrations">Thêm</a></nav><script>
(()=>{const $=s=>document.querySelector(s),esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const day=d=>{const x=new Date(d.getTime()-d.getTimezoneOffset()*60000);return x.toISOString().slice(0,10)};const now=new Date(),end=day(now),startDate=new Date(now);startDate.setDate(startDate.getDate()-6);const start=day(startDate);const status=(s)=>'<span class="status">'+esc(s||'UNKNOWN')+'</span>';async function get(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});let d;try{d=await r.json()}catch(_){throw Error('INVALID_RESPONSE '+r.status)}if(!r.ok)throw Error(d.error||'HTTP '+r.status);return d}function sectionError(node,e){node.innerHTML='<div class="empty err">Không tải được nguồn dữ liệu: '+esc(e.message)+'</div>'}Promise.allSettled([get('/api/properties'),get('/api/reviews'),get('/api/intakes'),get('/api/errors'),get('/api/analytics/report?from='+start+'&to='+end)]).then(results=>{const [p,r,i,e,a]=results.map(x=>x.status==='fulfilled'?x.value:null);const failures=results.map((x,n)=>x.status==='rejected'?['Property Core','Review','Intake','Errors','Analytics'][n]+': '+x.reason.message:null).filter(Boolean);const metrics=[['Property Core',p?.length??'—','Canonical đã qua Gate A'],['Review',r?.length??'—','Hàng chờ và lịch sử'],['Intake',i?.length??'—','Nguồn đã nhận'],['Sự kiện 7 ngày',a?.total_events??'—','Không phải số khách']];$('#kpis').innerHTML=metrics.map(x=>'<div class="card"><div class="muted">'+esc(x[0])+'</div><div class="metric">'+esc(x[1])+'</div><div class="muted">'+esc(x[2])+'</div></div>').join('');if(r){const rows=r.map(x=>'<tr><td>'+esc(x.intake_id)+'</td><td>'+esc(x.property_id||'Chưa có canonical')+'</td><td>'+status(x.state)+'</td><td>'+status(x.publication_state)+'</td><td>r'+esc(x.review_revision)+' · '+esc(x.content_batch_count)+' batch</td><td><a class="primary" style="display:inline-block;border-radius:8px;padding:7px 10px" href="/?review='+encodeURIComponent(x.review_id)+'#review-section">Mở duyệt Website</a></td></tr>').join('');$('#listing-table').innerHTML=rows?'<div class="tablewrap"><table><thead><tr><th>Intake</th><th>Property</th><th>Gate A</th><th>Website</th><th>Revision / Content</th><th>Quy trình Owner</th></tr></thead><tbody>'+rows+'</tbody></table></div>':'<div class="empty">Chưa có review.</div>'}else sectionError($('#listing-table'),results[1].reason);if(a){$('#traffic-data').innerHTML='<div class="grid">'+[['Tổng event',a.total_events],['Ngày',start+' → '+end],['Retention',a.retention_days+' ngày'],['Unique visitors','Chưa đo']].map(x=>'<div class="card"><div class="muted">'+esc(x[0])+'</div><b>'+esc(x[1])+'</b></div>').join('')+'</div><p class="muted">Theo hành vi: '+esc((a.by_event_type||[]).map(x=>x.event_type+': '+x.count).join(' · ')||'Chưa có dữ liệu')+'. CTA click không chứng minh cuộc gọi thành công.</p>'}else sectionError($('#traffic-data'),results[4].reason);if(a)$('#demand-data').innerHTML='<div class="empty">Search event đã nhận: '+esc((a.by_event_type||[]).filter(x=>x.event_type==='search_filter').reduce((n,x)=>n+x.count,0))+' · từ khóa thô: không lưu · zero-result count: chưa có contract.</div>';if(e){const rows=Array.isArray(e)?e:[];$('#monitor-data').innerHTML=rows.length?'<div class="tablewrap"><table><thead><tr><th>Mã lỗi</th><th>Trạng thái</th><th>Cập nhật</th></tr></thead><tbody>'+rows.map(x=>'<tr><td>'+esc(x.errorCode||x.code||'—')+'</td><td>'+esc(x.status||'—')+'</td><td>'+esc(x.updatedAt||'—')+'</td></tr>').join('')+'</tbody></table></div>':'<div class="empty">Không có lỗi trong queue hiện tại.</div>'}else sectionError($('#monitor-data'),results[3].reason);const states=[['Property Core',p?'KẾT NỐI':'CHƯA KẾT NỐI'],['Review/Gate A',r?'KẾT NỐI':'CHƯA KẾT NỐI'],['Intake',i?'KẾT NỐI':'CHƯA KẾT NỐI'],['Analytics',a?'KẾT NỐI':'CHƯA KẾT NỐI'],['CRM','CHƯA KẾT NỐI'],['Production','KHÔNG THAY ĐỔI']];$('#integrations-data').innerHTML='<div class="tablewrap"><table><thead><tr><th>Nguồn</th><th>Trạng thái hiện tại</th></tr></thead><tbody>'+states.map(x=>'<tr><td>'+esc(x[0])+'</td><td>'+status(x[1])+'</td></tr>').join('')+'</tbody></table></div>'+ (failures.length?'<p class="err">'+failures.map(esc).join('<br>')+'</p>':'');});})();
</script></body></html>'''.encode("utf-8")

ANALYTICS_UI = '''<script>
(()=>{const byId=id=>document.getElementById(id),from=byId('analytics-from'),to=byId('analytics-to'),status=byId('analytics-status'),results=byId('analytics-results');const day=d=>{const x=new Date(d.getTime()-d.getTimezoneOffset()*60000);return x.toISOString().slice(0,10)};const today=new Date();to.value=day(today);const start=new Date(today);start.setDate(start.getDate()-6);from.value=day(start);const group=(title,rows,keys)=>{const section=document.createElement('section'),h=document.createElement('h3'),ul=document.createElement('ul');h.textContent=title;section.append(h);if(!rows.length){const li=document.createElement('li');li.textContent='Chưa có';ul.append(li)}for(const row of rows){const li=document.createElement('li');li.textContent=keys.map(k=>row[k]??'—').join(' · ');ul.append(li)}section.append(ul);results.append(section)};byId('analytics-load').onclick=async()=>{status.textContent='Đang tải...';results.replaceChildren();const q=new URLSearchParams({from:from.value,to:to.value});for(const [id,key] of [['analytics-property','property_id'],['analytics-type','event_type'],['analytics-channel','channel']]){const v=byId(id).value.trim();if(v)q.set(key,v)}try{const r=await fetch('/api/analytics/report?'+q,{credentials:'same-origin',cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||'HTTP '+r.status);const total=document.createElement('p');total.className='status';total.textContent='Tổng lượt sự kiện: '+d.total_events;results.append(total);group('Theo ngày',d.by_day,['date','count']);group('Theo bất động sản và hành vi',d.by_property,['property_id','event_type','count']);group('Theo loại sự kiện',d.by_event_type,['event_type','count']);group('Kênh CTA',d.by_channel,['channel','count']);group('Nguồn đã làm sạch',d.by_source,['source','count']);status.textContent='Báo cáo đã tải; retention '+d.retention_days+' ngày. Không có số khách duy nhất.'}catch(e){status.textContent='Không tải được báo cáo: '+e.message}}})();
</script>'''.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "BMTAdmin/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        request_path = urlparse(getattr(self, "path", "")).path
        if request_path == "/api/analytics/events" or request_path.startswith("/__issue39_test_admin__"):
            return
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def issue39_test_proxy(self) -> bool:
        """Bearer-gated HTTPS path to a loopback-only isolated Admin test process."""
        prefix = "/__issue39_test_admin__"
        original = urlsplit(self.path)
        if original.path != prefix and not original.path.startswith(prefix + "/"):
            return False
        if os.environ.get("BMT_ENV", "") != "staging":
            self.send_json({"error": "Not found"}, 404)
            return True
        port_value = os.environ.get("BMT_TEST_NAMESPACE_PROXY_PORT", "").strip()
        token_path_value = os.environ.get("BMT_TEST_NAMESPACE_TOKEN_FILE", "").strip()
        if not port_value or not token_path_value:
            self.send_json({"error": "Not found"}, 404)
            return True
        try:
            port = int(port_value)
            token_path = Path(token_path_value)
            parent = token_path.parent
            namespace_root = parent.parent
            token_info = token_path.stat()
            if (port < 49152 or port > 65535
                    or token_path.is_symlink() or parent.is_symlink()
                    or namespace_root.is_symlink() or namespace_root.stat().st_uid != os.getuid()
                    or namespace_root.stat().st_mode & 0o777 != 0o700
                    or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077
                    or token_info.st_uid != os.getuid() or token_info.st_mode & 0o077
                    or not token_path.is_file()):
                raise ValueError
            token = token_path.read_text(encoding="ascii").strip()
            if len(token) < 32:
                raise ValueError
        except (OSError, ValueError):
            self.send_json({"error": "Test namespace unavailable"}, 503)
            return True
        scheme, _, supplied = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, token):
            self.send_json({"error": "Test namespace authentication required"}, 401,
                           headers={"WWW-Authenticate": "Bearer"})
            return True
        if self.command not in {"GET", "POST", "DELETE"}:
            self.send_json({"error": "Method not allowed"}, 405, headers={"Allow": "GET, POST, DELETE"})
            return True
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send_json({"error": "Invalid request length"}, 400)
            return True
        if length < 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.send_json({"error": "Request length rejected"}, 413)
            return True
        body = self.rfile.read(length) if length else None
        forwarded = {key: value for key, value in self.headers.items()
                     if key.lower() in {"accept", "content-type", "origin", "referer", "cookie", "x-requested-with"}
                     or key.lower().startswith("x-idempotency-") or key.lower().startswith("x-test-")
                     or key.lower().startswith("x-owner-")}
        target_path = original.path[len(prefix):] or "/"
        if original.query:
            target_path += "?" + original.query
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            connection.request(self.command, target_path, body=body, headers=forwarded)
            upstream = connection.getresponse()
            payload = upstream.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY:
                self.send_json({"error": "Test namespace response too large"}, 502)
                return True
            self.send_response(upstream.status)
            for key, value in upstream.getheaders():
                if key.lower() in {"content-type", "cache-control", "set-cookie", "location", "www-authenticate", "allow"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            self.send_json({"error": "Isolated test Admin unavailable"}, 502)
        finally:
            connection.close()
        return True

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

    def create_content_from_intake(self, intake_id: str, payload: dict[str, Any]) -> None:
        """One explicit Owner action: reuse Intake/Review, then one private 5-output request."""
        test_only = (os.environ.get("BMT_ENV") in {"test", "staging"} and os.environ.get("BMT_TEST_ONLY") == "1"
                     and _is_loopback_host(HOST) and bool(os.environ.get("SALA_E2E_ROOT"))
                     and Path(PRIVATE_ROOT).resolve().is_relative_to(Path(os.environ["SALA_E2E_ROOT"]).resolve()))
        owner_id = self.owner_id()
        if not owner_id or owner_id != (os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"):
            self.send_json({"error": "OWNER_CONTENT_APPROVAL_REQUIRED", "published": False}, 403)
            return
        request_key = payload.get("idempotency_key")
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_key):
            self.send_json({"error": "CONTENT_IDEMPOTENCY_KEY_REQUIRED", "published": False}, 400)
            return
        requested_evidence = None
        if payload.get("evidence") is not None:
            try:
                requested_evidence = validate_evidence_payload(payload["evidence"])
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc) or "CONTENT_EVIDENCE_INVALID", "published": False, "provider_calls": 0}, 422)
                return
        record = next((item for item in load_index() if item.get("id") == intake_id), None)
        if record is None:
            self.send_json({"error": "INTAKE_NOT_FOUND", "published": False}, 404)
            return
        if (not test_only and
                (record.get("source") == "TEST_ONLY" or any("TEST_ONLY" in str(record.get(k, "")) for k in ("sourceGroup", "note", "clientBatchId")))):
            self.send_json({"error": "TEST_ONLY_INTAKE_BLOCKED", "published": False}, 403)
            return
        missing_fields, missing_evidence = intake_content_readiness(record)
        candidate_preview = to_candidate(record)
        if candidate_preview.get("conflicts"):
            self.send_json({"error": "CONTENT_SOURCE_CONFLICT", "intake_id": intake_id,
                            "conflicts": candidate_preview["conflicts"], "provider_calls": 0,
                            "published": False}, 409)
            return
        stored_evidence = record.get("ownerEvidence") if isinstance(record.get("ownerEvidence"), dict) else None
        bound_evidence = None
        if stored_evidence and not missing_evidence:
            bound_evidence = {"rights_status": stored_evidence["rights_status"], "citations": [{
                "source_id": stored_evidence["source_id"], "claim": stored_evidence["claim"],
                "rights_status": stored_evidence["rights_status"]}]}
            try:
                bound_evidence = validate_evidence_payload(bound_evidence)
            except (TypeError, ValueError):
                missing_evidence = ["source_id", "claim", "rights_status"]
        if requested_evidence is not None and requested_evidence != bound_evidence:
            self.send_json({"error": "EVIDENCE_INTAKE_MISMATCH", "intake_id": intake_id,
                            "provider_calls": 0, "published": False}, 409)
            return
        requested_evidence = bound_evidence
        if missing_fields or missing_evidence:
            self.send_json({"error": "CONTENT_PREREQUISITES_REQUIRED", "intake_id": intake_id,
                            "missing_fields": missing_fields, "missing_evidence": missing_evidence,
                            "suggested_facts": record.get("preliminaryFacts", {}),
                            "input_revision": int(record.get("inputRevision", 1)),
                            "provider_calls": 0, "published": False}, 409)
            return
        if (os.environ.get("BMT_ENV") != "staging" and not test_only) or not _CONTENT_LIVE_ENABLED:
            self.send_json({"error": "CONTENT_PHASE_DISABLED", "intake_id": intake_id,
                            "provider_calls": 0, "published": False}, 403)
            return
        center = self.review_center_or_error()
        if center is None:
            return
        try:
            with PROPERTY_OPERATION_LOCK:
                package = process_intake(center.store, center, record, ai_gateway=None, operation_id=record["id"])
                live = center.packages.get(package["review_id"])
                if live is None:
                    self.send_json({"error": "REVIEW_PERSISTENCE_FAILED", "published": False}, 503)
                    return
                prior = next((entry for entry in live.get("content_generation_requests", [])
                              if entry.get("idempotency_key") == request_key), None)
                if prior:
                    if prior.get("status") == "SUCCEEDED":
                        self.send_json(prior["response"], 200); return
                    if prior.get("status") == "BLOCKED":
                        self.send_json(prior["response"], 409); return
                    self.send_json({"error": "CONTENT_REQUEST_OUTCOME_UNKNOWN", "review_id": live["review_id"],
                                    "published": False}, 409); return
                existing_evidence = live.get("evidence_revisions") or []
                if existing_evidence:
                    current_evidence = existing_evidence[-1].get("evidence") or {}
                    if requested_evidence is not None and requested_evidence != current_evidence:
                        self.send_json({"error": "EVIDENCE_REVISION_EXISTS", "review_id": live["review_id"],
                                        "evidence_revision_id": existing_evidence[-1].get("evidence_revision_id"),
                                        "provider_calls": 0, "published": False}, 409)
                        return
                elif requested_evidence is None:
                    self.send_json({"error": "CONTENT_PREREQUISITES_REQUIRED",
                                    "blockers": ["PROVENANCE_CITATIONS_REQUIRED", "CONTENT_RIGHTS_REQUIRED"],
                                    "intake_id": intake_id, "review_id": live["review_id"],
                                    "provider_calls": 0, "published": False}, 409)
                    return
                else:
                    evidence_revision_id = f"evidence-revision-{live['review_id']}-r1"
                    evidence_entry = {"evidence_revision_id": evidence_revision_id, "review_id": live["review_id"],
                                      "intake_id": intake_id, "evidence": requested_evidence,
                                      "created_at": now_iso(), "origin": "OWNER_CONFIRMED"}
                    live.setdefault("evidence_revisions", []).append(evidence_entry)
                    live["updated_at"] = evidence_entry["created_at"]
                    center._persist(live)
                    append_audit("review_evidence_create", live["review_id"], "succeeded", None, 1,
                                 operation_id=f"op-{evidence_revision_id}", idempotency_key=evidence_revision_id)
                generation_id = "generation-" + hashlib.sha256((intake_id + ":" + request_key).encode()).hexdigest()[:24]
                request_entry = {"idempotency_key": request_key, "generation_id": generation_id,
                                 "status": "IN_PROGRESS", "started_at": now_iso()}
                live.setdefault("content_generation_requests", []).append(request_entry)
                live["updated_at"] = request_entry["started_at"]
                center._persist(live)
        except (KeyError, TypeError, ValueError) as exc:
            self.send_json({"error": str(exc) or "INTAKE_PROCESS_FAILED", "published": False}, 422)
            return

        candidate = live.get("candidate") or {}
        facts = candidate.get("facts") if isinstance(candidate.get("facts"), dict) else {}
        source_text = candidate.get("raw", {}).get("text") or candidate.get("raw", {}).get("note") or ""
        evidence_rows = live.get("evidence_revisions") or []
        evidence = evidence_rows[-1].get("evidence", {}) if evidence_rows else {}
        citations = evidence.get("citations") if isinstance(evidence.get("citations"), list) else []
        blockers = []
        if candidate.get("conflicts"):
            blockers.append("SOURCE_CONFLICTS_REQUIRE_OWNER_REVIEW")
        if not source_text and not any(v not in (None, "") for v in facts.values()):
            blockers.append("CONTENT_SOURCE_REQUIRED")
        if len(source_text) > 320:
            blockers.append("SOURCE_TEXT_EXCEEDS_SINGLE_REQUEST_BUDGET")
        if evidence.get("rights_status") not in {"COMPANY", "PERSONAL", "APPROVED"}:
            blockers.append("CONTENT_RIGHTS_REQUIRED")
        if not citations:
            blockers.append("PROVENANCE_CITATIONS_REQUIRED")
        if blockers:
            response = {"error": "CONTENT_PREREQUISITES_REQUIRED", "blockers": blockers,
                        "intake_id": intake_id, "review_id": live["review_id"],
                        "state": live.get("state"), "published": False, "provider_calls": 0}
            request_entry.update({"status": "BLOCKED", "response": response, "finished_at": now_iso()})
            center._persist(live)
            self.send_json(response, 409)
            return

        ai9 = run_ai9_preview(live)
        result = build_generative_content_batch(
            generation_id=generation_id, facts=facts,
            provenance={"intake_id": intake_id, "review_id": live["review_id"], "citations": citations},
            rights_status=evidence.get("rights_status"), media=candidate.get("media") or [],
            provider=_AI_PROVIDER, model=_AI_PROVIDER.model, usage_sink=_USAGE_LEDGER,
            review_id=live["review_id"], source_text=source_text)
        if result.get("status") != "PASS" or len(result.get("variants", [])) != 5:
            response = {"error": "CONTENT_GENERATION_BLOCKED", "reason_codes": result.get("reason_codes", [result.get("status", "BLOCKED")]),
                        "intake_id": intake_id, "review_id": live["review_id"], "published": False,
                        "provider_calls": 1, "receipt": result.get("receipt")}
            request_entry.update({"status": "BLOCKED", "response": response, "finished_at": now_iso()})
            center._persist(live)
            self.send_json(response, 422)
            return

        facts_revision_id = f"source-facts-{live['review_id']}-r{live.get('review_revision', 1)}"
        batch = {"content_batch_id": result["content_batch_id"], "generation_id": generation_id,
                 "generation_mode": "OPENAI_ONE_REQUEST_PRIVATE_DRAFT", "status": "PRIVATE_DRAFT",
                 "intake_id": intake_id, "review_id": live["review_id"], "review_revision": int(live.get("review_revision", 1)),
                 "facts_revision_id": facts_revision_id, "property_id": None, "property_revision": None,
                 "request_idempotency_key": request_key, "normalized_facts": result.get("normalized_facts", []),
                 "missing_fields": result.get("missing_fields", []), "conflicts": result.get("conflicts", []),
                 "ai9_layers": {key: (value or {}).get("status") for key, value in ai9.get("layers", {}).items()},
                 "media_analysis": "NOT_SENT", "generation": result.get("receipt"), "variants": []}
        for source in result["variants"]:
            variant = dict(source)
            variant.update({"review_id": live["review_id"], "intake_id": intake_id,
                            "review_revision": batch["review_revision"], "facts_revision_id": facts_revision_id,
                            "property_id": None, "property_revision": None, "content_revision": 1,
                            "channel_approvals": [], "status": "DRAFT", "visibility": "INTERNAL_ONLY"})
            batch["variants"].append(variant)
        live.setdefault("content_batches", []).append(batch)
        live.setdefault("ai9_runs", []).append(ai9)
        response = {"status": batch["status"], "intake_id": intake_id, "review_id": live["review_id"],
                    "review_revision": batch["review_revision"], "content_batch_id": batch["content_batch_id"],
                    "facts_revision_id": facts_revision_id, "ai9_layers": batch["ai9_layers"],
                    "generation": batch["generation"], "variants": batch["variants"],
                    "media_analysis": "NOT_SENT", "mutated_property_store": False, "published": False}
        request_entry.update({"status": "SUCCEEDED", "response": response, "finished_at": now_iso()})
        live["updated_at"] = now_iso()
        center._persist(live)
        try:
            with PRIVATE_FILE_LOCK:
                intake_rows = load_index()
                intake_row = next((item for item in intake_rows if item.get("id") == intake_id), None)
                if intake_row is not None:
                    intake_row.update({"status": "CONTENT_CREATED", "contentReviewId": live["review_id"],
                                       "contentBatchId": batch["content_batch_id"], "updatedAt": now_iso()})
                    save_index(intake_rows)
        except OSError:
            append_audit("intake_content_status", intake_id, "failed", "INDEX_WRITE_FAILED", 1,
                         operation_id=f"op-{intake_id}-content-status", idempotency_key=f"{intake_id}:content-status")
        self.send_json(response, 201)

    def evidence_route(self, review_id: str, *, payload: dict[str, Any] | None = None) -> None:
        test_root = Path(os.environ.get("SALA_E2E_ROOT", "")).resolve()
        test_only = (os.environ.get("BMT_ENV") == "test" and os.environ.get("BMT_TEST_ONLY") == "1"
                     and _is_loopback_host(HOST) and bool(os.environ.get("SALA_E2E_ROOT"))
                     and Path(PRIVATE_ROOT).resolve().is_relative_to(test_root))
        if os.environ.get("BMT_ENV") != "staging" and not test_only:
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

    def content_route_post(self, review_id: str, action: str, payload: dict[str, Any]) -> None:
        center = self.review_center_or_error()
        if center is None:
            return
        owner_id = self.owner_id()
        if not owner_id or owner_id != (os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"):
            self.send_json({"error": "OWNER_CONTENT_APPROVAL_REQUIRED"}, 403)
            return
        package = center.packages.get(review_id)
        if package is None:
            self.send_json({"error": "REVIEW_NOT_FOUND"}, 404)
            return
        try:
            property_id = str((package.get("mutation_result") or {}).get("property_id") or "")
            prop = center.store.get(property_id) if property_id else None
            property_revision = int((prop or {}).get("property_revision") or 0)
            if action == "create":
                self.send_json({"error": "CREATE_CONTENT_FROM_INTAKE_ONLY", "published": False}, 409); return
            if action == "approve":
                raise ValueError("CONTENT_CHANNEL_APPROVAL_REQUIRED")
            if action in {"edit", "select", "approve-channel"}:
                variant_id = str(payload.get("content_variant_id") or "")
                batch = next((b for b in package.get("content_batches", []) if any(v.get("content_variant_id") == variant_id for v in b.get("variants", []))), None)
                variant = next((v for v in batch.get("variants", []) if v.get("content_variant_id") == variant_id), None) if batch else None
                if variant is None:
                    self.send_json({"error": "CONTENT_VARIANT_NOT_FOUND"}, 404); return
                if int(payload.get("expected_content_revision", -1)) != int(variant.get("content_revision", 0)):
                    self.send_json({"error": "STALE_CONTENT_REVISION"}, 409); return
                if action == "edit":
                    latest = _latest_public_release(center, property_id)[1] if property_id else None
                    if latest and latest.get("action") == "APPROVE_PUBLICATION":
                        raise ValueError("REVOKE_PUBLICATION_BEFORE_CONTENT_EDIT")
                    title, body = payload.get("title"), payload.get("body")
                    if not isinstance(title, str) or not title.strip() or len(title) > 180 or not isinstance(body, str) or not body.strip() or len(body) > 6000:
                        raise ValueError("CONTENT_FIELDS_INVALID")
                    variant.update({"title": title.strip(), "body": body.strip(), "content_revision": int(variant["content_revision"]) + 1, "status": "DRAFT", "edited_by": owner_id, "edited_at": now_iso()})
                    package.pop("selected_content", None)
                elif action == "select":
                    if package.get("state") != "APPROVED" or not property_id or not property_revision:
                        raise ValueError("GATE_A_PROPERTY_REQUIRED")
                    if int(batch.get("review_revision", -1)) != int(package.get("review_revision", -2)):
                        raise ValueError("CONTENT_FACTS_REVISION_STALE")
                    facts_revision_id = f"facts-{property_id}-r{property_revision}"
                    batch.update({"property_id": property_id, "property_revision": property_revision, "facts_revision_id": facts_revision_id})
                    for item in batch["variants"]:
                        item.update({"property_id": property_id, "property_revision": property_revision, "facts_revision_id": facts_revision_id})
                    variant["selected"] = True
                    package["selected_content_variant_id"] = variant_id
                    package.pop("selected_content", None)
                else:
                    if package.get("state") != "APPROVED" or not property_id or not property_revision:
                        raise ValueError("GATE_A_APPROVAL_REQUIRED")
                    if package.get("selected_content_variant_id") != variant_id:
                        raise ValueError("CONTENT_SELECTION_REQUIRED")
                    if (variant.get("property_id") != property_id or int(variant.get("property_revision") or 0) != property_revision
                            or variant.get("facts_revision_id") != f"facts-{property_id}-r{property_revision}"):
                        raise ValueError("CONTENT_FACTS_REVISION_STALE")
                    evidence = (package.get("evidence_revisions") or [])
                    current_evidence = evidence[-1].get("evidence", {}) if evidence else {}
                    gate_payload = {"classification": "AI_CONTENT_PACKAGE", "rights_status": current_evidence.get("rights_status"), "source_citation_ids": [x.get("source_id") for x in current_evidence.get("citations", []) if x.get("source_id")]}
                    gate = run_gate_b(gate_payload)
                    if gate.get("status") != "PASS":
                        self.send_json({"error": (gate.get("errors") or ["GATE_B_BLOCKED"])[0], "blockers": gate.get("errors", [])}, 422); return
                    channel = str(payload.get("channel") or "") if action == "approve-channel" else ""
                    if channel not in {item["channel"] for item in channel_registry()} | {"public_web"}:
                        raise ValueError("CONTENT_CHANNEL_UNSUPPORTED")
                    approved = approve_gate_b(gate_payload, gate, owner_id=owner_id)
                    variant["status"] = "OWNER_APPROVED"
                    channel_approval = {"channel": channel, "content_variant_id": variant_id,
                                        "content_revision": int(variant["content_revision"]),
                                        "facts_revision_id": variant["facts_revision_id"],
                                        "status": "OWNER_APPROVED_DRAFT", "approved_by": owner_id,
                                        "approved_at": approved["approved_at"], "gate_b": gate}
                    approvals = [x for x in variant.get("channel_approvals", []) if x.get("channel") != channel]
                    approvals.append(channel_approval)
                    variant["channel_approvals"] = approvals
                    if channel == "public_web":
                        package["selected_content"] = {"content_batch_id": variant["content_batch_id"],
                            "content_variant_id": variant_id, "content_revision": int(variant["content_revision"]),
                            "facts_revision_id": variant["facts_revision_id"], "property_id": property_id,
                            "property_revision": property_revision, "title": variant["title"], "body": variant["body"],
                            "status": approved["status"], "approved_by": owner_id, "approved_at": approved["approved_at"],
                            "gate_b": gate, "channel_approval": channel_approval}
                package["updated_at"] = now_iso(); center._persist(package)
                self.send_json({"status": variant.get("status"), "content_batch_id": variant.get("content_batch_id"), "content_variant_id": variant_id, "content_revision": variant.get("content_revision"), "channel_approvals": variant.get("channel_approvals", []), "published": False, "gate_b": variant.get("status") == "OWNER_APPROVED"}); return
        except (KeyError, TypeError, ValueError) as exc:
            self.send_json({"error": str(exc) or "CONTENT_ACTION_INVALID"}, 422)

    def review_route_get(self, path: str) -> bool:
        center = self.review_center_or_error()
        if center is None: return True
        center._load_packages()
        preview_media_match = re.fullmatch(r"/api/reviews/([^/]+)/public-release/media/([a-f0-9]{24})", path)
        if preview_media_match:
            try:
                snapshot = _public_release_snapshot(center, preview_media_match.group(1))
                if snapshot.get("blockers") or not snapshot.get("confirmation_preview"):
                    self.send_json({"error": "PUBLIC_CONFIRMATION_PREVIEW_BLOCKED"}, 404)
                    return True
                item = next((row for row in snapshot.get("confirmation_media", [])
                             if row.get("media_id") == preview_media_match.group(2)), None)
                if item is None:
                    self.send_json({"error": "PUBLIC_PREVIEW_MEDIA_NOT_FOUND"}, 404)
                    return True
                asset_root_value = os.environ.get("BMT_ADMIN_PUBLIC_ASSETS_ROOT", "").strip()
                asset_root = Path(asset_root_value).expanduser().resolve() if asset_root_value else PROJECT_ROOT.resolve()
                if not _is_public_asset_ref(item["media_ref"], str(snapshot["confirmation_preview"].get("slug") or "")):
                    self.send_json({"error": "PUBLIC_PREVIEW_MEDIA_NOT_FOUND"}, 404)
                    return True
                asset_path = (asset_root / item["media_ref"]).resolve(strict=True)
                asset_path.relative_to(asset_root)
                data = asset_path.read_bytes()
                if len(data) > 25 * 1024 * 1024:
                    self.send_json({"error": "PUBLIC_PREVIEW_MEDIA_TOO_LARGE"}, 413)
                    return True
                content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
                if content_type not in {"image/jpeg", "image/png", "image/webp", "image/avif"}:
                    self.send_json({"error": "PUBLIC_PREVIEW_MEDIA_TYPE_INVALID"}, 404)
                    return True
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            except (KeyError, OSError, ValueError, ProjectionError):
                self.send_json({"error": "PUBLIC_PREVIEW_MEDIA_NOT_FOUND"}, 404)
            return True
        if path == "/api/reviews":
            rows = []
            for package in center.packages.values():
                property_id = str((package.get("mutation_result") or {}).get("property_id") or "")
                core = center.store.get(property_id) if property_id else None
                current_revision = int((core or {}).get("property_revision") or 0)
                publication_state = "UNKNOWN"
                try:
                    release_package, latest = _latest_public_release(center, property_id) if property_id else (None, None)
                except ValueError:
                    release_package, latest = None, None
                    publication_state = "UNKNOWN_CONFLICT"
                if publication_state == "UNKNOWN_CONFLICT":
                    pass
                elif not latest:
                    publication_state = "NOT_APPROVED"
                elif latest.get("action") == "REVOKE_PUBLICATION":
                    publication_state = "REVOKED"
                elif int(latest.get("property_revision") or 0) != current_revision:
                    publication_state = "STALE"
                else:
                    publication_state = ("PUBLISHED" if latest.get("action") == "APPROVE_PUBLICATION"
                                         and release_package and release_package.get("review_id") == package["review_id"]
                                         else "ACTIVE_OTHER_REVIEW" if latest.get("action") == "APPROVE_PUBLICATION"
                                         else "NOT_APPROVED")
                rows.append({"review_id": package["review_id"], "state": package["state"],
                             "review_revision": package["review_revision"], "property_id": property_id or None,
                             "property_revision": current_revision or None, "publication_state": publication_state,
                             "candidate_id": (package.get("candidate") or {}).get("candidate_id"),
                             "intake_id": (package.get("candidate") or {}).get("intake_id"),
                             "source": (package.get("candidate") or {}).get("source"),
                             "content_batch_count": len(package.get("content_batches", [])),
                             "updated_at": package["updated_at"]})
            self.send_json(rows); return True
        media_match = re.fullmatch(r"/api/reviews/([^/]+)/media-rights", path)
        if media_match:
            try: self.send_json(_media_rights_snapshot(center, media_match.group(1)))
            except KeyError as exc: self.send_json({"error": str(exc.args[0])}, 404)
            return True
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
            try:
                package = center.view(match.group(1))
                mutation = package.get("mutation_result") or {}
                property_id = str(mutation.get("property_id") or "")
                prop = center.store.get(property_id) if property_id else None
                property_revision = int((prop or {}).get("property_revision") or 0)
                for batch in package.get("content_batches", []):
                    for variant in batch.get("variants", []):
                        try:
                            variant["channel_previews"] = build_channel_drafts(
                                variant, property_id=property_id, property_revision=property_revision)
                        except ValueError:
                            variant["channel_previews"] = []
                package["content_channel_registry"] = channel_registry()
                self.send_json(package)
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
            expected_content_variant_id = payload.get("expected_content_variant_id")
            expected_content_revision = payload.get("expected_content_revision")
            if expected_content_revision is not None:
                expected_content_revision = int(expected_content_revision)
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
                selected_content = snapshot.get("selected_content") or {}
                if selected_content and (expected_content_variant_id != selected_content.get("content_variant_id")
                        or expected_content_revision != selected_content.get("content_revision")):
                    self.send_json({"error": "STALE_PUBLIC_CONTENT_REVISION"}, 409)
                    return
                if not selected_content and (expected_content_variant_id is not None or expected_content_revision is not None):
                    self.send_json({"error": "STALE_PUBLIC_CONTENT_REVISION"}, 409)
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
                        projection_result = build_public_projection(center.store, property_id, canonical_path, public_content=package.get("selected_content"))
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
                    staging_public = os.environ.get("BMT_ENV") == "staging"
                    self.send_json({
                        "status": "STAGING_PUBLISHED" if staging_public else "PREVIEW_READY",
                        "environment": os.environ.get("BMT_ENV", "unknown"), "published": staging_public, "review_id": review_id,
                        "property_id": property_id, "review_revision": expected_review_revision,
                        "property_revision": expected_property_revision,
                        "decision_owner": owner_id,
                        "projection_id": projection_result["projection"]["projectionId"],
                        "publication_id": projection_result["projection"]["publicationId"],
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
        content_match = re.fullmatch(r"/api/reviews/(review-[a-f0-9]+)/content/(create|edit|select|approve|approve-channel)", path)
        if content_match:
            if not _CONTENT_LIVE_ENABLED:
                self.send_json({"error": "CONTENT_PHASE_DISABLED"}, 404); return True
            if not isinstance(payload, dict):
                self.send_json({"error": "CONTENT_REQUEST_INVALID"}, 400); return True
            self.content_route_post(content_match.group(1), content_match.group(2), payload)
            return True
        media_match = re.fullmatch(r"/api/reviews/([^/]+)/media-rights", path)
        if media_match:
            if not isinstance(payload, dict): self.send_json({"error":"MEDIA_RIGHTS_REQUEST_INVALID"},400); return True
            try:
                with PROPERTY_OPERATION_LOCK: result=_approve_appearance_media(center,media_match.group(1),payload,self.owner_id() or "")
                self.send_json(result,201)
            except PermissionError as exc: self.send_json({"error":str(exc)},403)
            except KeyError as exc: self.send_json({"error":str(exc.args[0])},404)
            except (OSError,ValueError) as exc: self.send_json({"error":str(exc) or "MEDIA_RIGHTS_FAILED"},422)
            return True
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
        if self.issue39_test_proxy():
            return
        path = urlparse(self.path).path
        if path == "/api/health":
            self.runtime_json(); return
        if path == "/website":
            if not self.require_auth():
                return
            body = WEBSITE_MASTER_PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
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
            body = page()
            body = body.replace(b"</style></head>", HOME_CSS.encode("utf-8") + b"</style></head>", 1)
            body = body.replace(b"<main>", b"<main>" + HOME_HTML.encode("utf-8"), 1)
            body = body.replace(b'<section class="card"><p class="kicker">QUEUE</p>', b'<section id="queue-section" class="card"><p class="kicker">QUEUE</p>', 1)
            body = body.replace(b"</main><script>", MISSING_FIELDS_MARKUP.encode("utf-8") + b"</main><script>", 1)
            body = body.replace(b"</main><script>", ANALYTICS_CARD + b"</main><script>", 1)
            body = body.replace(b'</script></body></html>', b'</script>' + HOME_SCRIPT.encode("utf-8") + CONTENT_STUDIO_UI + PUBLIC_RELEASE_UI + ANALYTICS_UI + b'</body></html>', 1)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif not self.require_auth():
            return
        elif path == "/api/admin/status":
            self.send_json({"environment": os.environ.get("BMT_ENV", "unknown"),
                            "content_live_enabled": bool(_CONTENT_LIVE_ENABLED),
                            "website_state": "NOT_VERIFIED",
                            "published": False})
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
            center = self.review_center_or_error()
            if center is None:
                return
            records = center.store.list_properties()
            terms = [x for x in re.split(r"\s+", q) if x]
            result = []
            for item in records if isinstance(records, list) else []:
                hay = json.dumps({k: item.get(k) for k in ("property_id", "title", "address", "area", "property_type", "price", "lifecycle_state")}, ensure_ascii=False).lower()
                if not terms or all(t in hay for t in terms):
                    result.append({"property_id": item.get("property_id"), "property_revision": item.get("property_revision"),
                                   "title": item.get("title"), "source": "Property Core", "location": item.get("area"),
                                   "addressText": item.get("address"), "state": item.get("lifecycle_state")})
            self.send_json(result)
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        if self.issue39_test_proxy():
            return
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
            remove_index_record(intake_id)
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

    def do_PUT(self) -> None:
        if self.issue39_test_proxy():
            return
        self.send_error(501)

    def do_POST(self) -> None:
        if self.issue39_test_proxy():
            return
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
        if path == "/api/admin/credential-preflight":
            if not self.require_auth():
                return
            if os.environ.get("BMT_ENV") != "staging":
                self.send_json({"status": "BLOCKED", "error": "STAGING_ONLY", "request_sent": False}, 403)
                return
            expected_owner = os.environ.get("BMT_ADMIN_OWNER_ID", "owner").strip() or "owner"
            if self.owner_id() != expected_owner:
                self.send_json({"status": "BLOCKED", "error": "OWNER_SESSION_REQUIRED", "request_sent": False}, 403)
                return
            try:
                request_payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json({"status": "BLOCKED", "error": "INVALID_PREFLIGHT_REQUEST", "request_sent": False}, 400)
                return
            if request_payload != {}:
                self.send_json({"status": "BLOCKED", "error": "INVALID_PREFLIGHT_REQUEST", "request_sent": False}, 400)
                return
            global _CREDENTIAL_PREFLIGHT_LAST_AT
            now = time.monotonic()
            with _CREDENTIAL_PREFLIGHT_LOCK:
                remaining = 15.0 - (now - _CREDENTIAL_PREFLIGHT_LAST_AT)
                if remaining > 0:
                    self.send_json({"status": "BLOCKED", "error": "PREFLIGHT_RATE_LIMITED",
                                    "retry_after_seconds": int(remaining) + 1, "request_sent": False}, 429)
                    return
                _CREDENTIAL_PREFLIGHT_LAST_AT = now
            try:
                credential = resolve_openai_key(allow_env_fallback=False)
                ready = bool(credential)
                del credential
                if not ready:
                    self.send_json({"status": "MISSING", "error": "KEYCHAIN_CREDENTIAL_MISSING",
                                    "request_sent": False}, 503)
                    return
                self.send_json({"status": "READY", "credential_source": "KEYCHAIN",
                                "request_sent": False, "secret_returned": False})
            except CredentialError as exc:
                status = "MISSING" if exc.code == "KEYCHAIN_CREDENTIAL_MISSING" else "BLOCKED"
                self.send_json({"status": status, "error": exc.code, "os_status": exc.os_status,
                                "request_sent": False, "secret_returned": False}, 503)
            return
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
            if not _CONTENT_LIVE_ENABLED:
                self.send_json({"error": "CONTENT_PHASE_DISABLED"}, 404); return
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
            if not _CONTENT_LIVE_ENABLED:
                self.send_json({"error": "CONTENT_PHASE_DISABLED"}, 404); return
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
            if not self.require_auth():
                return
            test_root_value = os.environ.get("SALA_E2E_ROOT", "")
            test_root = Path(test_root_value).resolve() if test_root_value else None
            test_only_enabled = (
                os.environ.get("BMT_ENV") in {"test", "staging"}
                and os.environ.get("BMT_TEST_ONLY") == "1"
                and _is_loopback_host(HOST)
                and test_root is not None
                and Path(PRIVATE_ROOT).resolve().is_relative_to(test_root)
            )
            if not test_only_enabled:
                self.send_json({"error": "TEST_ONLY_ROUTE_DISABLED"}, 404); return
            center = self.review_center_or_error()
            if center is None:
                return
            fixture = {"source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY synthetic AI9 fixture"}
            record = None
            cleanup_errors = []
            try:
                record, _, _ = create_intake_record(fixture, [], supplied_key="ai9-server-test-only:" + uuid.uuid4().hex, client_batch_id=None)
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
                        cleanup_errors.append("REVIEW_CLEANUP_FAILED")
                    try:
                        remove_index_record(record["id"])
                    except Exception:
                        cleanup_errors.append("INTAKE_INDEX_CLEANUP_FAILED")
                    try:
                        folder = (INBOX_ROOT / record["id"]).resolve()
                        if folder.parent == INBOX_ROOT.resolve() and folder.exists():
                            shutil.rmtree(folder)
                    except Exception:
                        cleanup_errors.append("INTAKE_FILES_CLEANUP_FAILED")
            if cleanup_errors:
                self.send_json({"error": "TEST_ONLY_CLEANUP_INCOMPLETE", "cleanup_errors": cleanup_errors}, 500); return
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
        one_touch_match = re.fullmatch(r"/api/intakes/([^/]+)/create-content", path)
        if one_touch_match:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json({"error": "INVALID_CONTENT_REQUEST_JSON", "published": False}, 400)
                return
            if not isinstance(payload, dict):
                self.send_json({"error": "INVALID_CONTENT_REQUEST_JSON", "published": False}, 400)
                return
            self.create_content_from_intake(one_touch_match.group(1), payload)
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
            current_revision = int(record.get("inputRevision", 1))
            expected_revision = payload.get("expected_input_revision")
            if expected_revision is not None and expected_revision != current_revision:
                self.send_json({"error": "STALE_INTAKE_REVISION", "input_revision": current_revision}, 409); return
            incoming_facts = payload.get("facts", {})
            draft_only_fields = {"transaction_type", "price_cycle"}
            allowed_draft_fields = set(KNOWN_FIELDS) | draft_only_fields
            if not isinstance(incoming_facts, dict) or any(key not in allowed_draft_fields for key in incoming_facts):
                self.send_json({"error": "INTAKE_FACTS_INVALID"}, 400); return
            facts = dict(record.get("preliminaryFacts") or {})
            for key, value in incoming_facts.items():
                if isinstance(value, bool) or not isinstance(value, (str, int, float)) or (isinstance(value, str) and len(value) > 500):
                    self.send_json({"error": "INTAKE_FACT_VALUE_INVALID", "field": key}, 400); return
                facts[key] = value.strip() if isinstance(value, str) else value
            if incoming_facts:
                record["preliminaryFacts"] = facts
                record["ownerFacts"] = {**(record.get("ownerFacts") or {}), **incoming_facts}
                record["ownerFactsRevision"] = int(record.get("ownerFactsRevision", 0)) + 1
            incoming_evidence = payload.get("evidence")
            if incoming_evidence is not None:
                if not isinstance(incoming_evidence, dict):
                    self.send_json({"error": "CONTENT_EVIDENCE_INVALID"}, 400); return
                evidence = {"source_id": str(incoming_evidence.get("source_id") or "").strip(),
                            "claim": str(incoming_evidence.get("claim") or "").strip(),
                            "rights_status": str(incoming_evidence.get("rights_status") or "").strip().upper()}
                if (len(evidence["source_id"]) > 160 or len(evidence["claim"]) > 1200
                        or evidence["rights_status"] not in {"", "PERSONAL", "COMPANY", "APPROVED"}):
                    self.send_json({"error": "CONTENT_EVIDENCE_INVALID"}, 400); return
                if evidence != record.get("ownerEvidence"):
                    record["ownerEvidence"] = evidence if any(evidence.values()) else None
                    record["ownerEvidenceRevision"] = int(record.get("ownerEvidenceRevision", 0)) + 1
            now = now_iso()
            draft = {"facts": facts, "ownerEvidenceRevision": int(record.get("ownerEvidenceRevision", 0)),
                     "ownerFactsRevision": int(record.get("ownerFactsRevision", 0)),
                     "createdAt": (record.get("draft") or {}).get("createdAt", now), "updatedAt": now}
            record["draft"] = draft; record["status"] = "DRAFT_NEEDS_REVIEW"; record["inputRevision"] = current_revision + 1; record["updatedAt"] = now
            save_index(items)
            folder = INBOX_ROOT / record["id"]
            if folder.is_dir() and folder.parent == INBOX_ROOT:
                draft_path = folder / "draft.json"; tmp_path = folder / f".draft.{uuid.uuid4().hex}.tmp"
                tmp_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); tmp_path.chmod(0o600); tmp_path.replace(draft_path)
            append_audit("draft_save", record["id"], "succeeded", None, 1, operation_id=f"op-{record['id']}-draft", idempotency_key=f"{record['id']}:draft")
            self.send_json({"id": record["id"], "status": record["status"], "draft": draft,
                            "input_revision": record["inputRevision"], "owner_evidence_revision": record.get("ownerEvidenceRevision", 0)}, 201); return
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
            record, replay, upload = create_intake_record(fields, files, supplied_key=supplied_key, client_batch_id=client_batch_id)
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
        self.send_json({**record, "upload": upload, "idempotent_replay": replay}, 200 if replay else 201)


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
