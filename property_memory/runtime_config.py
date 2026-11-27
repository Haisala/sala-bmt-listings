"""Small, fail-closed runtime identity contract for the BĐS backend."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

ENVIRONMENTS = frozenset({"dev", "test", "staging", "prod"})
API_VERSION = "v1"
DEFAULT_SERVICE = "hai-sala-bds"
DEFAULT_VERSION = "p0a-1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    service: str
    version: str
    api_version: str = API_VERSION


def load_runtime_config(environ: dict[str, str] | None = None) -> RuntimeConfig:
    values = os.environ if environ is None else environ
    environment = str(values.get("BMT_ENV", "dev")).strip().lower()
    if environment not in ENVIRONMENTS:
        raise ValueError("CONFIG_INVALID_ENVIRONMENT")
    service = str(values.get("BMT_SERVICE_NAME", DEFAULT_SERVICE)).strip()
    version = str(values.get("BMT_BACKEND_VERSION", DEFAULT_VERSION)).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", service):
        raise ValueError("CONFIG_INVALID_SERVICE")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
        raise ValueError("CONFIG_INVALID_VERSION")
    return RuntimeConfig(environment=environment, service=service, version=version)


def health_payload(config: RuntimeConfig | None = None) -> dict[str, str]:
    config = config or load_runtime_config()
    return {
        "status": "ok",
        "service": config.service,
        "environment": config.environment,
        "version": config.version,
        "api_version": config.api_version,
        "timestamp": _now(),
    }


def diagnostic_payload(config: RuntimeConfig | None = None) -> dict[str, str | bool]:
    config = config or load_runtime_config()
    return {
        "status": "ready",
        "service": config.service,
        "environment": config.environment,
        "version": config.version,
        "api_version": config.api_version,
        "compatibility": "API_AVAILABLE",
        "transport": "backend_reachable",
        "review_service": "configured_by_runtime",
        "timestamp": _now(),
    }
