"""TEST_ONLY deterministic Vietnamese source-prefill and authenticated HTTP contract."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import admin.admin_server as admin
from property_memory.source_prefill import extract_source_facts


def main() -> None:
    villa = extract_source_facts(
        "Biệt thự mặt hồ EcoCity tại EcoCity, Buôn Ma Thuột; đất 250m² (10x25), "
        "xây dựng 400m², giá bán 16,5 tỷ",
        intake_id="TEST_ONLY-villa",
        source_revision=3,
    )
    assert villa["facts"]["property_type"] == "HOUSE"
    assert villa["facts"]["title"] == "Biệt thự mặt hồ EcoCity"
    assert villa["facts"]["address"] == "EcoCity, Buôn Ma Thuột"
    assert villa["facts"]["area"] == "250 m²"
    assert villa["facts"]["land_area"] == 250
    assert villa["facts"]["construction_area"] == 400
    assert villa["facts"]["width"] == 10 and villa["facts"]["length"] == 25
    assert villa["facts"]["price_vnd"] == 16_500_000_000
    assert villa["provenance"]["price_vnd"]["source_revision"] == 3
    assert villa["provenance"]["price_vnd"]["evidence_excerpt"] == "16,5 tỷ"
    assert villa["conflicts"] == []

    rental = extract_source_facts(
        "Cho thuê căn hộ 72 m² ở Trung tâm, Buôn Ma Thuột. Giá 6 triệu/tháng",
        intake_id="TEST_ONLY-rental",
    )
    assert rental["facts"]["transaction_type"] == "RENT"
    assert rental["facts"]["address"] == "Trung tâm, Buôn Ma Thuột"
    assert rental["facts"]["price_vnd_per_month"] == 6_000_000
    assert rental["facts"]["price_cycle"] == "MONTH"

    decimal_source = "Biệt thự song lập mặt hồ điều hòa EcoCity - 16,5 tỷ; đất 250m² (10x25), xây dựng 400m²"
    decimal_candidate = extract_source_facts(decimal_source, intake_id="TEST_ONLY-decimal", source_revision=4)
    assert decimal_candidate["facts"]["title"] == "Biệt thự song lập mặt hồ điều hòa EcoCity"
    assert "16,5" not in decimal_candidate["facts"]["title"]
    assert "price_vnd" not in decimal_candidate["facts"]
    assert decimal_candidate["price_candidates"] == [{
        "amount_vnd": 16_500_000_000, "evidence_excerpt": "16,5 tỷ",
        "source_revision": 4, "transaction_unconfirmed": True,
    }]

    labeled = extract_source_facts(
        "Bán biệt thự EcoCity. Diện tích đất: 250 m², diện tích xây dựng: 400 m². "
        "Giá bán 16,5 tỷ; giá tham khảo 16,5 tỷ.",
        intake_id="TEST_ONLY-labeled",
    )
    assert labeled["facts"]["land_area"] == 250
    assert labeled["facts"]["construction_area"] == 400
    assert labeled["facts"]["area"] == "250 m²"
    assert labeled["facts"]["price_vnd"] == 16_500_000_000
    assert labeled["conflicts"] == []

    undecided = {
        "id": "TEST_ONLY-transaction-undecided",
        "note": "Biệt thự EcoCity; diện tích đất: 250 m²; giá 16,5 tỷ",
        "preliminaryFacts": {},
        "media": [],
    }
    projected = admin.project_intake_prefill(undecided)
    missing_fields, missing_rights = admin.intake_content_readiness(projected)
    assert "transaction_type" in missing_fields and "price_vnd" not in missing_fields
    assert missing_rights == ["rights_status"]
    assert projected["priceCandidates"][0]["amount_vnd"] == 16_500_000_000
    for transaction, expected_price in (("SALE", "price_vnd"), ("RENT", "price_vnd_per_month")):
        selected = {**undecided, "ownerFacts": {"transaction_type": transaction}}
        selected_projected = admin.project_intake_prefill(selected)
        selected_missing, _ = admin.intake_content_readiness(selected_projected)
        assert selected_projected["preliminaryFacts"][expected_price] == 16_500_000_000
        assert selected_projected["factProvenance"][expected_price]["origin"] == "SOURCE_CANDIDATE_BOUND_BY_OWNER_TRANSACTION"
        assert expected_price not in selected_missing
        assert ("price_cycle" in selected_missing) is False
        assert selected_projected["preliminaryFacts"].get("price_cycle") == ("MONTH" if transaction == "RENT" else None)

    conflict = extract_source_facts(
        "Bán hay cho thuê căn hộ; diện tích 70 m² hoặc 80 m²; giá 2 tỷ hoặc 3 tỷ",
        intake_id="TEST_ONLY-conflict",
    )
    conflict_fields = {row["field"] for row in conflict["conflicts"]}
    assert {"transaction_type", "area"} <= conflict_fields
    assert conflict_fields & {"price_vnd", "price_vnd_per_month"}
    assert "rights_status" not in conflict["facts"]
    assert "legal_status" not in conflict["facts"]

    with tempfile.TemporaryDirectory(prefix="sala-prefill-http-") as folder:
        root = Path(folder)
        root.chmod(0o700)
        private = root / "private"
        private.mkdir(mode=0o700)
        inbox = private / "inbox"
        inbox.mkdir(mode=0o700)
        note = ("Biệt thự mặt hồ EcoCity tại EcoCity, Buôn Ma Thuột; đất 250m² (10x25), "
                "xây dựng 400m², giá bán 16,5 tỷ")
        with patch.object(admin, "PRIVATE_ROOT", private), \
             patch.object(admin, "INBOX_ROOT", inbox), \
             patch.object(admin, "INDEX_PATH", private / "index.json"), \
             patch.dict(os.environ, {"BMT_ADMIN_OWNER_ID": "TEST_ONLY-owner"}):
            saved, replayed, _ = admin.create_intake_record(
                {"source": "TEST_ONLY", "sourceGroup": "TEST_ONLY-prefill", "note": note},
                [("front.png", b"\x89PNG\r\n\x1a\nTEST_ONLY-image")],
                supplied_key="TEST_ONLY-prefill-http-idem-0001",
            )
            assert not replayed
            assert saved["ownerEvidence"]["rights_status"] == ""
            assert saved["ownerEvidence"]["source_id"].startswith("intake:")
            assert saved["preliminaryFacts"]["price_vnd"] == 16_500_000_000
            assert saved["media"][0]["mediaId"]

            session = "TEST_ONLY-session-prefill"
            admin.SESSIONS[session] = time.time() + 300
            admin.SESSION_OWNERS[session] = "TEST_ONLY-owner"
            server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                request = urllib.request.Request(base + "/api/intakes")
                try:
                    urllib.request.urlopen(request, timeout=4)
                    raise AssertionError("unauthenticated intake GET unexpectedly succeeded")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 401
                headers = {"Cookie": f"bmt_admin_session={session}"}
                before_read = (private / "index.json").read_bytes()
                with urllib.request.urlopen(urllib.request.Request(base + "/api/intakes", headers=headers), timeout=4) as response:
                    assert response.status == 200
                    rows = json.loads(response.read())
                assert (private / "index.json").read_bytes() == before_read
                row = next(item for item in rows if item["id"] == saved["id"])
                assert row["preliminaryFacts"]["title"] == "Biệt thự mặt hồ EcoCity"
                assert row["preliminaryFacts"]["area"] == "250 m²"
                assert row["preliminaryFacts"]["construction_area"] == 400
                assert row["factProvenance"]["price_vnd"]["intake_id"] == saved["id"]
                assert row["ownerEvidence"]["rights_status"] == ""
                with urllib.request.urlopen(base + "/assets/brand/full-dark.png", timeout=4) as response:
                    assert response.status == 200 and response.headers.get_content_type() == "image/png"
                    assert response.read(8) == b"\x89PNG\r\n\x1a\n"
                with urllib.request.urlopen(urllib.request.Request(base + "/", headers=headers), timeout=4) as response:
                    home = response.read().decode("utf-8")
                assert home.count('class="home-metric"') == 4
                assert home.count('class="home-actions"') == 1 and "Hành động nhanh" in home
                assert home.count("<li>") >= 5
                assert 'class="home-bottom-nav"' in home and "display:none" in home
                assert 'id="home-dashboard"' in home and 'data-screen-view="home"' not in home
                assert 'data-screen-link="intake"' in home and 'data-screen-link="content"' in home
                assert 'href="#intake-section"' not in home and 'href="#review-section"' not in home
                assert 'data-dynamic-price' in home and 'SOURCE_CANDIDATE_BOUND_BY_OWNER_TRANSACTION' not in home
                assert "function selectedScreen()" in home and "window.navigateAdminScreen=navigateScreen" in home
                assert "row.dataset.intakeId=x.id" in home and "navigateScreen('content',{intakeId:row.id" in home
                assert "16,5" not in home
                assert "owner-analytics" not in home and 'id="analytics-from"' not in home
                assert "data-home-ai-preflight" not in home and "source_id" in home
                with urllib.request.urlopen(urllib.request.Request(base + "/website", headers=headers), timeout=4) as response:
                    website = response.read().decode("utf-8")
                traffic = website.index('id="traffic"')
                analytics = website.index('id="owner-analytics"')
                demand = website.index('id="demand"')
                assert traffic < analytics < demand
                assert all(f'id="{field}"' in website for field in (
                    "analytics-from", "analytics-to", "analytics-property", "analytics-type", "analytics-channel"))
                media_url = base + f"/api/intakes/{saved['id']}/media/{saved['media'][0]['mediaId']}"
                with urllib.request.urlopen(urllib.request.Request(media_url, headers=headers), timeout=4) as response:
                    assert response.status == 200 and response.headers.get("Cache-Control") == "private, no-store"
                    assert response.read().startswith(b"\x89PNG")
                try:
                    urllib.request.urlopen(urllib.request.Request(base + f"/api/intakes/{saved['id']}/media/media-{'0'*24}", headers=headers), timeout=4)
                    raise AssertionError("unlinked media identifier unexpectedly succeeded")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 404
            finally:
                server.shutdown()
                thread.join(timeout=4)
                server.server_close()
                admin.SESSIONS.pop(session, None)
                admin.SESSION_OWNERS.pop(session, None)
    print("INTAKE_PREFILL_HTTP_TEST_ONLY_PASS")


if __name__ == "__main__":
    main()
