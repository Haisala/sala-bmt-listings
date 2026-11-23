"""Contract checks for draft-only social channel adapters."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from property_memory.content_channels import build_channel_drafts, channel_registry


def main() -> None:
    registry = channel_registry()
    assert [item["channel"] for item in registry] == [
        "facebook", "zalo_company", "tiktok", "instagram", "youtube", "x", "linkedin"
    ]
    assert all(item["connection_status"] == "NOT_CONNECTED"
               and item["dispatch_status"] == "DRAFT_ONLY"
               and item["dispatch_enabled"] is False for item in registry)

    variant = {
        "content_batch_id": "batch-TEST_ONLY-1", "content_variant_id": "variant-TEST_ONLY-1",
        "content_revision": 2, "facts_revision_id": "facts-property-TEST_ONLY-r3",
        "property_id": "property-TEST_ONLY", "property_revision": 3,
        "status": "OWNER_APPROVED", "body": "TEST_ONLY caption", "channel_approvals": [],
    }
    first = build_channel_drafts(variant, property_id="property-TEST_ONLY", property_revision=3)
    replay = build_channel_drafts(variant, property_id="property-TEST_ONLY", property_revision=3)
    assert len(first) == 7 and [x["channel_draft_id"] for x in first] == [x["channel_draft_id"] for x in replay]
    assert all(item["caption"] == "TEST_ONLY caption" and item["published"] is False
               and item["media_assets"] == [] and item["media_status"] == "NOT_ATTACHED_OWNER_SELECTION_REQUIRED"
               and item["format_metadata"]["platform_limits"] == "NOT_CONFIGURED"
               and item["approval_status"] == "PENDING_OWNER_APPROVAL" for item in first)

    variant["channel_approvals"] = [{"channel": "facebook", "content_variant_id": variant["content_variant_id"],
                                     "content_revision": 2, "status": "OWNER_APPROVED_DRAFT"}]
    approved = build_channel_drafts(variant, property_id="property-TEST_ONLY", property_revision=3)
    assert approved[0]["approval_status"] == "OWNER_APPROVED_DRAFT"
    assert all(item["approval_status"] == "PENDING_OWNER_APPROVAL" for item in approved[1:])
    variant["content_revision"] = 3
    revised = build_channel_drafts(variant, property_id="property-TEST_ONLY", property_revision=3)
    assert revised[0]["channel_draft_id"] != first[0]["channel_draft_id"]

    for kwargs, expected in [
        ({"property_id": "property-other", "property_revision": 3}, "CHANNEL_CONTENT_PROPERTY_STALE"),
        ({"property_id": "property-TEST_ONLY", "property_revision": 4}, "CHANNEL_CONTENT_PROPERTY_STALE"),
    ]:
        try:
            build_channel_drafts(variant, **kwargs)
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("stale property lineage must fail closed")
    try:
        build_channel_drafts(variant, property_id="property-TEST_ONLY", property_revision=3,
                             media_assets=[{"media_id": "media-test", "rights_status": "UNKNOWN"}])
    except ValueError as exc:
        assert str(exc) == "CHANNEL_MEDIA_RIGHTS_REQUIRED"
    else:
        raise AssertionError("unapproved media must fail closed")
    print("CONTENT_CHANNEL_CONTRACT PASS: 7 draft-only channels; stable lineage; approval/media guards")


if __name__ == "__main__":
    main()
