"""Shared allowlist and validator for Admin-produced public property DTOs."""
from __future__ import annotations

import re
from typing import Any

REQUIRED = {"id", "slug", "title", "status", "price", "features", "images", "updatedAt", "propertyId", "propertyRevision", "projectionId", "publicationId", "projectionGeneratedAt", "sourceSystem", "publicationStatus", "publicationRevision", "publicationApprovedAt", "mediaRights"}
STATUSES = {"Đang bán", "Đang cho thuê", "Đã cho thuê", "Đã cọc", "Đã bán"}
PRIVATE_KEYS = {"internal_analysis", "candidate", "source_bundle", "owner_contact", "credentials", "private_media_path", "rights_evidence"}
ALLOWED_FIELDS = {"id", "slug", "title", "status", "price", "features", "images", "updatedAt", "category", "propertyType", "addressText", "areas", "transactionType", "priceLabel", "priceNote", "priceUnit", "area", "dimensions", "width", "length", "landArea", "legal", "road", "description", "videos", "reelUrl", "createdAt", "locationNotes", "featured", "goodPrice", "propertyId", "propertyRevision", "projectionId", "publicationId", "projectionGeneratedAt", "sourceSystem", "publicationStatus", "publicationRevision", "publicationApprovedAt", "mediaRights", "contentBatchId", "contentVariantId", "contentRevision"}


class PublicContractError(ValueError):
    pass


def validate_public_dto(dto: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED - set(dto))
    if missing:
        raise PublicContractError("MISSING_REQUIRED:" + ",".join(missing))
    if int(dto["propertyRevision"]) < 1:
        raise PublicContractError("PROPERTY_REVISION_REQUIRED")
    if not re.fullmatch(r"publication-[a-f0-9]{32}", str(dto["publicationId"])):
        raise PublicContractError("PUBLICATION_ID_INVALID")
    if dto["sourceSystem"] != "SALA_PROPERTY_CORE":
        raise PublicContractError("PUBLIC_SOURCE_REQUIRED")
    if (dto["publicationStatus"] != "APPROVED"
            or int(dto["publicationRevision"]) != int(dto["propertyRevision"])
            or not isinstance(dto["publicationApprovedAt"], str)
            or not dto["publicationApprovedAt"].strip()):
        raise PublicContractError("PUBLIC_RELEASE_APPROVAL_INVALID_OR_STALE")
    if not isinstance(dto["images"], list) or not isinstance(dto["mediaRights"], dict):
        raise PublicContractError("PUBLIC_MEDIA_RIGHTS_REQUIRED")
    if set(dto["mediaRights"]) != set(dto["images"]):
        raise PublicContractError("PUBLIC_MEDIA_RIGHTS_REQUIRED")
    if any(value != "APPROVED_PUBLIC" for value in dto["mediaRights"].values()):
        raise PublicContractError("PUBLIC_MEDIA_RIGHTS_NOT_APPROVED")
    if dto["status"] not in STATUSES:
        raise PublicContractError("INVALID_STATUS")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(dto["slug"])):
        raise PublicContractError("INVALID_SLUG")
    content_keys = {"contentBatchId", "contentVariantId", "contentRevision"}
    if content_keys.intersection(dto) and (not content_keys.issubset(dto) or not all(isinstance(dto[key], str) and dto[key] for key in ("contentBatchId", "contentVariantId")) or int(dto["contentRevision"]) < 1):
        raise PublicContractError("PUBLIC_CONTENT_LINEAGE_INVALID")
    if not isinstance(dto["price"], (int, float)) or isinstance(dto["price"], bool) or dto["price"] <= 0:
        raise PublicContractError("INVALID_PRICE")
    leaked = sorted(PRIVATE_KEYS.intersection(dto))
    if leaked:
        raise PublicContractError("PRIVATE_FIELD:" + ",".join(leaked))
    unexpected = sorted(set(dto) - ALLOWED_FIELDS)
    if unexpected:
        raise PublicContractError("FIELD_NOT_ALLOWLISTED:" + ",".join(unexpected))
    return dto


def to_legacy_web_record(dto: dict[str, Any]) -> dict[str, Any]:
    validate_public_dto(dto)
    return {key: value for key, value in dto.items() if key in ALLOWED_FIELDS}
