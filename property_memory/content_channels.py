"""Pre-wired, draft-only channel adapters; no provider calls or dispatch side effects."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

CHANNEL_ADAPTER_VERSION = "sala-content-channel-v1"
CHANNEL_REGISTRY: tuple[dict[str, Any], ...] = (
    {"channel": "facebook", "label": "Facebook"},
    {"channel": "zalo_company", "label": "Zalo"},
    {"channel": "tiktok", "label": "TikTok"},
    {"channel": "instagram", "label": "Instagram"},
    {"channel": "youtube", "label": "YouTube"},
    {"channel": "x", "label": "X"},
    {"channel": "linkedin", "label": "LinkedIn"},
)


def channel_registry() -> list[dict[str, Any]]:
    return [{**item, "adapter_version": CHANNEL_ADAPTER_VERSION,
             "connection_status": "NOT_CONNECTED", "dispatch_status": "DRAFT_ONLY",
             "dispatch_enabled": False, "capabilities": ["caption_preview", "owner_approval"]}
            for item in CHANNEL_REGISTRY]


def build_channel_drafts(variant: dict[str, Any], *, property_id: str,
                         property_revision: int,
                         media_assets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Create stable per-channel caption/format previews without assuming platform limits."""
    required = ("content_batch_id", "content_variant_id", "facts_revision_id", "body")
    if not property_id or type(property_revision) is not int or property_revision < 1:
        raise ValueError("CHANNEL_PROPERTY_REVISION_REQUIRED")
    if any(not isinstance(variant.get(key), str) or not variant[key].strip() for key in required):
        raise ValueError("CHANNEL_CONTENT_LINEAGE_REQUIRED")
    content_revision = variant.get("content_revision")
    if type(content_revision) is not int or content_revision < 1:
        raise ValueError("CHANNEL_CONTENT_REVISION_REQUIRED")
    if variant.get("property_id") != property_id or int(variant.get("property_revision") or 0) != property_revision:
        raise ValueError("CHANNEL_CONTENT_PROPERTY_STALE")
    if variant.get("facts_revision_id") != f"facts-{property_id}-r{property_revision}":
        raise ValueError("CHANNEL_CONTENT_FACTS_STALE")
    media = media_assets or []
    if not isinstance(media, list) or any(
        not isinstance(item, dict) or item.get("rights_status") != "APPROVED_PUBLIC"
        or not isinstance(item.get("media_id"), str) or not item["media_id"]
        for item in media
    ):
        raise ValueError("CHANNEL_MEDIA_RIGHTS_REQUIRED")
    approvals = variant.get("channel_approvals") if isinstance(variant.get("channel_approvals"), list) else []
    drafts = []
    for channel in channel_registry():
        code = channel["channel"]
        approved = any(isinstance(item, dict) and item.get("channel") == code
                       and item.get("content_variant_id") == variant["content_variant_id"]
                       and item.get("content_revision") == content_revision
                       and item.get("status") == "OWNER_APPROVED_DRAFT" for item in approvals)
        seed = ":".join((property_id, str(property_revision), variant["content_batch_id"],
                          variant["content_variant_id"], str(content_revision), code))
        drafts.append({
            "channel_draft_id": "channel-draft-" + sha256(seed.encode()).hexdigest()[:24],
            "channel": code, "label": channel["label"],
            "property_id": property_id, "property_revision": property_revision,
            "content_batch_id": variant["content_batch_id"],
            "content_variant_id": variant["content_variant_id"],
            "content_revision": content_revision,
            "facts_revision_id": variant["facts_revision_id"],
            "caption": variant["body"],
            "format_metadata": {"profile": "UNCONFIGURED", "assumptions": [],
                                "platform_limits": "NOT_CONFIGURED"},
            "media_assets": list(media),
            "media_status": "ATTACHED_APPROVED" if media else "NOT_ATTACHED_OWNER_SELECTION_REQUIRED",
            "approval_status": "OWNER_APPROVED_DRAFT" if approved else "PENDING_OWNER_APPROVAL",
            "connection_status": "NOT_CONNECTED", "dispatch_status": "DRAFT_ONLY",
            "published": False,
        })
    return drafts
