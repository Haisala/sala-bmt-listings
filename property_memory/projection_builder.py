"""Private, allowlisted projection from approved canonical properties.

This module deliberately does not know the production public JSON path.  Callers
must provide an isolated output path during private integration tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from .public_contract_adapter import STATUSES as PUBLIC_STATUSES
from .review_center import public_release_id
from .store import PropertyStore, is_publishable_by_rights


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FIELDS = frozenset({
    "id", "propertyId", "propertyRevision", "projectionId", "publicationId", "projectionGeneratedAt",
    "slug", "title", "category", "propertyType", "addressText", "status", "price",
    "priceLabel", "priceNote", "area", "dimensions", "width", "length", "landArea",
    "legal", "road", "features", "description", "images", "videos", "reelUrl",
    "createdAt", "updatedAt", "areas", "locationNotes", "featured", "goodPrice",
    "transactionType", "priceUnit", "sourceSystem", "publicationStatus",
    "publicationRevision", "publicationApprovedAt", "mediaRights", "contentBatchId", "contentVariantId", "contentRevision",
})


class ProjectionError(ValueError):
    pass


def _slug(value: str) -> str:
    import re
    text = value.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "property"


def _price_label(price: Any, *, price_type: str | None = None) -> str | None:
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    label = f"{price:,.0f} VNĐ".replace(",", ".")
    return f"{label}/tháng" if price_type == "RENT" else f"Giá chào {label}"


def _approval_evidence(store: PropertyStore, property_id: str, property_revision: int) -> dict[str, Any]:
    rows = store.connection.execute(
        "SELECT package_json FROM review_packages WHERE state = 'APPROVED' ORDER BY updated_at DESC"
    ).fetchall()
    candidates: list[tuple[str, str, int, dict[str, Any], dict[str, Any]]] = []
    gate_a_match = False
    for row in rows:
        package = json.loads(row[0])
        result = package.get("mutation_result") or {}
        decision = package.get("decision") or {}
        if (result.get("property_id") != property_id or decision.get("type") != "APPROVE"
                or int(decision.get("revision", -1)) != int(package.get("review_revision", -2))):
            continue
        gate_a_match = True
        for index, release in enumerate(package.get("public_release_decisions", [])):
            if release.get("property_id") == property_id:
                candidates.append((str(release.get("at") or ""), str(package.get("review_id") or ""), index, package, release))
    if not candidates:
        raise ProjectionError("PUBLIC_RELEASE_APPROVAL_REQUIRED" if gate_a_match else "APPROVAL_EVIDENCE_REQUIRED")
    latest_time = max(entry[0] for entry in candidates)
    latest = [entry for entry in candidates if entry[0] == latest_time]
    if len({entry[1] for entry in latest}) > 1:
        raise ProjectionError("PUBLIC_RELEASE_ORDER_AMBIGUOUS")
    _, _, _, package, release = max(latest, key=lambda entry: entry[2])
    if release.get("action") == "REVOKE_PUBLICATION":
        raise ProjectionError("PUBLIC_RELEASE_REVOKED")
    if release.get("action") != "APPROVE_PUBLICATION":
        raise ProjectionError("PUBLIC_RELEASE_APPROVAL_REQUIRED")
    if int(release.get("property_revision") or 0) != property_revision:
        raise ProjectionError("PUBLIC_RELEASE_APPROVAL_STALE")
    return release


def _is_public_asset_ref(ref: str, public_slug: str) -> bool:
    """Accept only an already-published, existing asset locator for this listing."""
    from pathlib import PurePosixPath

    if not ref or "\\" in ref or "://" in ref or ref.startswith("/"):
        return False
    path = PurePosixPath(ref)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if len(path.parts) != 4 or path.parts[:2] != ("assets", "images"):
        return False
    if path.parts[2] != public_slug or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
        return False
    configured_root = os.environ.get("BMT_ADMIN_PUBLIC_ASSETS_ROOT", "").strip()
    if os.environ.get("BMT_ENV") == "staging" and not configured_root:
        return False
    asset_root = Path(configured_root).expanduser().resolve() if configured_root else PROJECT_ROOT.resolve()
    resolved = (asset_root / Path(*path.parts)).resolve()
    try:
        resolved.relative_to(asset_root)
    except ValueError:
        return False
    return resolved.is_file()


def _media_rights_allowed(store: PropertyStore, property_id: str, appearance_id: str | None) -> bool:
    # Prefer the latest appearance-specific decision. Only use a property-wide
    # grant when no decision exists for this source appearance; a later denial
    # therefore overrides an earlier approval for that same appearance.
    rows = []
    if appearance_id:
        rows = store.connection.execute(
            "SELECT rights_status, approved FROM rights_refs WHERE property_id = ? AND appearance_id = ? AND channel = 'public_web' ORDER BY rowid DESC LIMIT 1",
            (property_id, appearance_id),
        ).fetchall()
    if not rows:
        rows = store.connection.execute(
            "SELECT rights_status, approved FROM rights_refs WHERE property_id = ? AND appearance_id IS NULL AND channel = 'public_web' ORDER BY rowid DESC LIMIT 1",
            (property_id,),
        ).fetchall()
    return bool(rows and is_publishable_by_rights(rows[0][0], approved=bool(rows[0][1])))


def _public_media(store: PropertyStore, property_id: str, public_slug: str | None = None) -> list[str]:
    prop = store.get(property_id) or {}
    slug = public_slug or _slug(str(prop.get("title") or property_id))
    media: list[str] = []
    for item in store.list_media_history(property_id):
        if not item.get("active") or not _media_rights_allowed(store, property_id, item.get("appearance_id")):
            continue
        ref = str(item.get("media_ref", ""))
        # Rights approval never converts an internal source locator into a public URL.
        if not _is_public_asset_ref(ref, slug):
            continue
        if ref not in media:
            media.append(ref)
    return media


def _dto(store: PropertyStore, prop: dict[str, Any], release: dict[str, Any], public_content: dict[str, Any] | None = None) -> dict[str, Any]:
    revision = int(prop.get("property_revision") or 0)
    if revision < 1:
        raise ProjectionError("PROPERTY_REVISION_REQUIRED")
    appearances = store.list_source_appearances(prop["property_id"])
    if not appearances:
        raise ProjectionError("PROVENANCE_APPEARANCE_REQUIRED")
    # Stable lineage for replay: the canonical update timestamp is immutable for
    # a given fixture/revision and avoids a new public artifact on every retry.
    seed = f"{prop['property_id']}:{revision}".encode("utf-8")
    projection_id = "projection-" + hashlib.sha256(seed).hexdigest()[:24]
    generated_at = prop.get("updated_at")
    attributes = prop.get("attributes") if isinstance(prop.get("attributes"), dict) else {}
    canonical_title = prop.get("title") or prop["property_id"]
    title = (public_content or {}).get("title") or canonical_title
    public_slug = attributes.get("public_slug") or _slug(str(canonical_title))
    price_type = attributes.get("price_type")
    is_rent = price_type == "RENT"
    default_status = "Đang cho thuê" if is_rent else "Đang bán"
    status = attributes.get("public_status") or default_status
    allowed_for_transaction = {"Đang cho thuê", "Đã cho thuê"} if is_rent else {"Đang bán", "Đã cọc", "Đã bán"}
    if status not in PUBLIC_STATUSES or status not in allowed_for_transaction:
        raise ProjectionError("PUBLIC_STATUS_UNSUPPORTED")
    features = prop.get("features") if isinstance(prop.get("features"), list) else []
    media = _public_media(store, prop["property_id"], public_slug)
    dto = {
        "id": public_slug, "propertyId": prop["property_id"], "propertyRevision": revision,
        "projectionId": projection_id, "publicationId": release.get("publication_id") or public_release_id(release),
        "projectionGeneratedAt": generated_at,
        "sourceSystem": "SALA_PROPERTY_CORE", "publicationStatus": "APPROVED",
        "publicationRevision": revision, "publicationApprovedAt": release.get("at"),
        "mediaRights": {ref: "APPROVED_PUBLIC" for ref in media},
        "slug": public_slug, "title": title, "category": "Nhà đất",
        "propertyType": prop.get("property_type"), "addressText": prop.get("address"),
        "status": status, "price": prop.get("price"), "priceLabel": _price_label(prop.get("price"), price_type=price_type),
        "priceUnit": "VND/tháng" if is_rent else "VND", "transactionType": "RENTAL" if is_rent else "SALE",
        "priceNote": "Giá chào theo dữ liệu được Owner duyệt; cần xác minh trước giao dịch.",
        "area": prop.get("area"), "dimensions": f"{prop.get('width')}×{prop.get('length')}m" if prop.get("width") and prop.get("length") else None,
        "width": prop.get("width"), "length": prop.get("length"), "landArea": prop.get("land_area"),
        "legal": prop.get("legal_status"), "road": prop.get("road_access"), "features": features,
        "description": (public_content or {}).get("body") or prop.get("factual_description") or title, "images": media, "videos": [], "reelUrl": None,
        "createdAt": prop.get("created_at"), "updatedAt": generated_at, "areas": [prop.get("area")] if prop.get("area") else [],
        "locationNotes": [], "featured": False, "goodPrice": False,
    }
    if public_content:
        dto.update({"contentBatchId": public_content["content_batch_id"],
                    "contentVariantId": public_content["content_variant_id"],
                    "contentRevision": int(public_content["content_revision"])})
    if set(dto) - PUBLIC_FIELDS:
        raise ProjectionError("PUBLIC_ALLOWLIST_VIOLATION")
    return dto


def build_public_projection(store: PropertyStore, property_id: str, output_path: str | Path, *, public_content: dict[str, Any] | None = None, failure: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Atomically upsert one approved property into an isolated public fixture."""
    destination = Path(output_path).resolve()
    if destination == (PROJECT_ROOT / "data" / "listings.json").resolve():
        raise ProjectionError("PRODUCTION_OUTPUT_BLOCKED")
    prop = store.get(property_id)
    if prop is None:
        raise ProjectionError("PROPERTY_NOT_FOUND")
    release = _approval_evidence(store, property_id, int(prop.get("property_revision") or 0))
    if public_content and (public_content.get("status") != "OWNER_APPROVED" or public_content.get("property_id") != property_id
            or int(public_content.get("property_revision") or 0) != int(prop.get("property_revision") or 0)
            or not all(isinstance(public_content.get(key), str) and public_content[key].strip() for key in ("content_batch_id", "content_variant_id", "facts_revision_id", "title", "body"))
            or int(public_content.get("content_revision") or 0) < 1
            or public_content.get("facts_revision_id") != f"facts-{property_id}-r{int(prop.get('property_revision') or 0)}"):
        raise ProjectionError("APPROVED_CONTENT_STALE_OR_INVALID")
    dto = _dto(store, prop, release, public_content)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing: list[Any] = []
    if destination.exists():
        with destination.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if not isinstance(existing, list):
            raise ProjectionError("PUBLIC_DATA_MUST_BE_ARRAY")
    replaced = False
    result: list[Any] = []
    for item in existing:
        if isinstance(item, dict) and item.get("propertyId") == property_id:
            result.append(dto); replaced = True
        else:
            result.append(item)
    if not replaced:
        result.append(dto)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    backup = destination.with_name(destination.name + ".bak")
    if destination.exists():
        shutil.copyfile(destination, backup)
    if failure:
        failure("before_replace")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {"projection": dto, "replaced": replaced, "idempotent_replay": replaced and any(x == dto for x in existing if isinstance(x, dict))}


def rollback_projection(output_path: str | Path) -> None:
    destination = Path(output_path).resolve()
    backup = destination.with_name(destination.name + ".bak")
    if not backup.exists():
        raise ProjectionError("PROJECTION_BACKUP_NOT_FOUND")
    os.replace(backup, destination)
