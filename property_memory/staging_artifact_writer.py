"""Cô lập ghi artifact Web staging sau Public DTO V1 adapter.

Chặn đúng production artifact trong checkout; hỗ trợ data/listings.json ở preview temp.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from .public_contract_adapter import to_legacy_web_record

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StagingArtifactError(ValueError):
    pass


def _guard(path: Path) -> None:
    if path.resolve() == (PROJECT_ROOT / "data" / "listings.json").resolve():
        raise StagingArtifactError("PRODUCTION_OUTPUT_BLOCKED")


def write_staging_web_artifact(
    dto: dict[str, Any],
    output_path: str | Path,
    *,
    failure: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Atomically upsert one adapted listing into an isolated JSON artifact."""
    destination = Path(output_path).resolve()
    _guard(destination)
    record = to_legacy_web_record(dto)
    # Preserve lineage for staging audits; Customer Web ignores these fields.
    for key in ("propertyId", "propertyRevision", "projectionId", "projectionGeneratedAt"):
        record[key] = dto[key]

    existing: list[Any] = []
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            raise StagingArtifactError("PUBLIC_DATA_MUST_BE_ARRAY")

    replaced = False
    result: list[Any] = []
    for item in existing:
        if isinstance(item, dict) and (
            item.get("propertyId") == record["propertyId"]
            or item.get("id") == record["id"]
            or item.get("slug") == record["slug"]
        ):
            previous_revision = int(item.get("propertyRevision") or 0)
            if previous_revision > int(record["propertyRevision"]):
                raise StagingArtifactError("STALE_REVISION_OUTPUT_BLOCKED")
            result.append(record)
            replaced = True
        else:
            result.append(item)
    if not replaced:
        result.append(record)

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + ".bak")
    if destination.exists():
        shutil.copyfile(destination, backup)
    if failure:
        failure("before_replace")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {"path": str(destination), "replaced": replaced, "records": len(result), "record": record}


def withdraw_staging_web_record(output_path: str | Path, property_id: str) -> dict[str, int]:
    """Remove one canonical property from an isolated Website preview artifact."""
    destination = Path(output_path).resolve()
    _guard(destination)
    if not property_id:
        raise StagingArtifactError("PROPERTY_ID_REQUIRED")
    if not destination.exists():
        return {"removed": 0}
    existing = json.loads(destination.read_text(encoding="utf-8"))
    if not isinstance(existing, list):
        raise StagingArtifactError("PUBLIC_DATA_MUST_BE_ARRAY")
    filtered = [item for item in existing if not (isinstance(item, dict) and item.get("propertyId") == property_id)]
    removed = len(existing) - len(filtered)
    if not removed:
        return {"removed": 0}
    backup = destination.with_name(destination.name + ".bak")
    shutil.copyfile(destination, backup)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(filtered, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {"removed": removed}


def rollback_staging_web_artifact(output_path: str | Path) -> None:
    destination = Path(output_path).resolve()
    backup = destination.with_name(destination.name + ".bak")
    if not backup.exists():
        raise StagingArtifactError("STAGING_BACKUP_NOT_FOUND")
    os.replace(backup, destination)
