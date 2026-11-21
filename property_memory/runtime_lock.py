"""Fail-closed runtime identity validation, opt-in for isolated staging only."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class RuntimeLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeManifest:
    environment: str
    instance_id: str
    checkout: str
    revision: str
    host: str
    port: int
    database: str
    private_root: str


def load_manifest(path: str | Path) -> RuntimeManifest:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ("environment", "instance_id", "checkout", "revision", "host", "port", "database", "private_root")
    if any(not value.get(key) for key in required):
        raise RuntimeLockError("RUNTIME_MANIFEST_INCOMPLETE")
    return RuntimeManifest(
        environment=str(value["environment"]), instance_id=str(value["instance_id"]),
        checkout=str(Path(value["checkout"]).resolve()), revision=str(value["revision"]),
        host=str(value["host"]), port=int(value["port"]),
        database=str(Path(value["database"]).resolve()),
        private_root=str(Path(value["private_root"]).resolve()),
    )


def validate_runtime_lock(*, manifest_path: str | Path | None = None, environ: dict[str, str] | None = None) -> RuntimeManifest | None:
    values = os.environ if environ is None else environ
    if str(values.get("BMT_RUNTIME_LOCK", "0")).lower() not in {"1", "true", "yes"}:
        return None
    path = manifest_path or values.get("BMT_RUNTIME_MANIFEST")
    if not path:
        raise RuntimeLockError("RUNTIME_MANIFEST_REQUIRED")
    manifest = load_manifest(path)
    expected = {
        "BMT_ENV": manifest.environment,
        "BMT_CHECKOUT": manifest.checkout,
        "BMT_BACKEND_REVISION": manifest.revision,
        "BMT_ADMIN_HOST": manifest.host,
        "BMT_ADMIN_PORT": str(manifest.port),
        "BMT_ADMIN_PRIVATE_ROOT": manifest.private_root,
        "BMT_ADMIN_DB_PATH": manifest.database,
    }
    for key, wanted in expected.items():
        if str(values.get(key, "")).strip() != wanted:
            raise RuntimeLockError("RUNTIME_IDENTITY_MISMATCH:" + key)
    if manifest.environment != "staging" or manifest.host != "127.0.0.1" or manifest.port != 18876:
        raise RuntimeLockError("STAGING_BINDING_REQUIRED")
    if Path(manifest.database).parent != Path(manifest.private_root):
        raise RuntimeLockError("DATABASE_PRIVATE_ROOT_MISMATCH")
    return manifest
