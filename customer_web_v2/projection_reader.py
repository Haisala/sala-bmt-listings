"""Read-only Customer Web adapter for Admin's staged Public DTO artifact."""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .public_contract import PublicContractError, validate_public_dto
except ImportError:
    from public_contract import PublicContractError, validate_public_dto

MAX_PROJECTION_BYTES = 10 * 1024 * 1024
MAX_PUBLIC_MEDIA_BYTES = 25 * 1024 * 1024
AREA_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:m2|m²|sqm|sq\.?\s*m)?\s*$", re.IGNORECASE)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


class ProjectionUnavailable(RuntimeError):
    pass


class PublicProjectionReader:
    """Fail-closed public reader. It never queries the Customer Web SQLite store."""

    def __init__(self, projection_path: str | Path | None = None, assets_root: str | Path | None = None):
        self.projection_path = Path(projection_path).expanduser() if projection_path else None
        self.assets_root = Path(assets_root).expanduser() if assets_root else None

    @staticmethod
    def _media_id(dto: dict[str, Any], ref: str) -> str:
        seed = f"{dto['propertyId']}:{dto['propertyRevision']}:{ref}".encode("utf-8")
        return "pubmed-" + hashlib.sha256(seed).hexdigest()[:24]

    def _asset(self, ref: str, slug: str) -> tuple[Path, str] | None:
        if self.assets_root is None or not isinstance(ref, str) or "\\" in ref or "://" in ref:
            return None
        rel = PurePosixPath(ref)
        if (rel.is_absolute() or len(rel.parts) != 4 or rel.parts[:2] != ("assets", "images")
                or rel.parts[2] != slug or any(part in {"", ".", ".."} for part in rel.parts)
                or Path(rel.name).suffix.lower() not in IMAGE_EXTENSIONS):
            return None
        try:
            root = self.assets_root.resolve(strict=True)
            path = (root / Path(*rel.parts)).resolve(strict=True)
            path.relative_to(root)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PUBLIC_MEDIA_BYTES:
                return None
        except (OSError, ValueError):
            return None
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/avif"}:
            return None
        return path, mime

    @staticmethod
    def _area(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            match = AREA_RE.fullmatch(value)
            if not match:
                return None
            number = float(match.group(1).replace(",", "."))
        else:
            return None
        return number if math.isfinite(number) and 0 < number <= 1_000_000 else None

    def _load(self) -> list[dict[str, Any]]:
        path = self.projection_path
        if path is None:
            raise ProjectionUnavailable("PUBLIC_PROJECTION_NOT_CONFIGURED")
        try:
            if path.is_symlink():
                raise ProjectionUnavailable("PUBLIC_PROJECTION_INVALID")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROJECTION_BYTES:
                raise ProjectionUnavailable("PUBLIC_PROJECTION_INVALID")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ProjectionUnavailable:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectionUnavailable("PUBLIC_PROJECTION_UNAVAILABLE") from exc
        if not isinstance(raw, list):
            raise ProjectionUnavailable("PUBLIC_PROJECTION_INVALID")

        valid: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                valid.append(validate_public_dto(item))
            except (PublicContractError, TypeError, ValueError, KeyError):
                continue

        # Ambiguous duplicate identity or slug blocks every involved record.
        counts: dict[tuple[str, str], int] = {}
        for item in valid:
            for key, value in (("property", item["propertyId"]), ("id", item["id"]), ("projection", item["projectionId"])):
                counts[(key, str(value))] = counts.get((key, str(value)), 0) + 1
        return [item for item in valid if all(counts[(key, str(value))] == 1 for key, value in (
            ("property", item["propertyId"]), ("id", item["id"]), ("projection", item["projectionId"]))) ]

    def _record(self, dto: dict[str, Any]) -> dict[str, Any] | None:
        transaction = dto.get("transactionType")
        if transaction not in {"SALE", "RENTAL"}:
            return None
        area = self._area(dto.get("area"))
        price = dto.get("price")
        if (area is None or isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(float(price)) or price <= 0
                or not isinstance(dto.get("title"), str) or not dto["title"].strip()
                or not isinstance(dto.get("propertyType"), str) or not dto["propertyType"].strip()
                or not isinstance(dto.get("addressText"), str) or not dto["addressText"].strip()):
            return None
        media: list[dict[str, str]] = []
        for ref in dto["images"]:
            asset = self._asset(ref, dto["slug"])
            if asset is None:
                continue
            media.append({"media_id": self._media_id(dto, ref), "mime": asset[1]})
        return {
            "listing_id": dto["id"], "property_id": dto["propertyId"],
            "property_revision": int(dto["propertyRevision"]), "projection_id": dto["projectionId"],
            "publication_id": dto["publicationId"],
            "publication_revision": int(dto["publicationRevision"]),
            "publication_approved_at": dto["publicationApprovedAt"],
            "status": "APPROVED", "transaction_type": "RENT" if transaction == "RENTAL" else "SALE",
            "title": dto["title"], "description": str(dto.get("description") or dto["title"]),
            "content_batch_id": dto.get("contentBatchId"), "content_variant_id": dto.get("contentVariantId"),
            "content_revision": dto.get("contentRevision"),
            "price_vnd": int(price), "area_m2": area, "property_type": dto["propertyType"],
            "location": dto["addressText"], "media": media,
        }

    def list_listings(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        rows = [record for dto in self._load() if (record := self._record(dto)) is not None]
        q = query.get("q", [""])[0].strip().casefold()[:160]
        transaction = query.get("transaction_type", [""])[0].upper()
        if q:
            rows = [item for item in rows if q in " ".join((item["title"], item["description"], item["location"])).casefold()]
        if transaction in {"SALE", "RENT"}:
            rows = [item for item in rows if item["transaction_type"] == transaction]
        return rows

    def get_listing(self, listing_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_listings({}) if item["listing_id"] == listing_id), None)

    def media(self, media_id: str) -> tuple[str, bytes] | None:
        for dto in self._load():
            record = self._record(dto)
            if record is None:
                continue
            for ref in dto["images"]:
                if self._media_id(dto, ref) != media_id:
                    continue
                asset = self._asset(ref, dto["slug"])
                if asset is None:
                    return None
                try:
                    data = asset[0].read_bytes()
                except OSError:
                    return None
                if len(data) > MAX_PUBLIC_MEDIA_BYTES:
                    return None
                return asset[1], data
        return None
