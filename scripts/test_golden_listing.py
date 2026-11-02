#!/usr/bin/env python3
"""Deterministic contract checks for the public golden listing journey."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
slug = "nha-xanh-co-ban-cu-ebur"

def main():
    data = json.loads((ROOT / "data/listings.json").read_text())
    listing = next(x for x in data if x["slug"] == slug)
    assert len(listing["images"]) == 10
    assert listing["price"] == 1_850_000_000
    assert listing["dimensions"] == "5×27m"
    ecocity = next(x for x in data if x["slug"] == "can-ho-ecocity-ton-duc-thang-7-trieu")
    assert ecocity.get("images") == []

    app = (ROOT / "app.js").read_text()
    home = (ROOT / "index.html").read_text()
    shell = (ROOT / "listing.html").read_text()
    route = (ROOT / "listings" / slug / "index.html").read_text()
    public = "\n".join([app, home, shell, route])
    for marker in ("gallery-swipe", "gallery-indicator", "savedView", "refreshSaveButtons", "Có thể bạn cũng quan tâm", "Lưu căn này"):
        assert marker in public, marker
    assert "new URL(`${B}listings/" in app
    assert "../../app.js" in route and 'data-page="detail"' in route
    assert "https://haisala.io.vn/listings/nha-xanh-co-ban-cu-ebur/" in route
    assert "thang máy" not in public.lower() and "elevator" not in public.lower()
    assert "AI_INTERNAL_ANALYSIS" not in public and "Deal Score" not in public
    print("GOLDEN LISTING PASS: route, 10 images, save/deep-link contract, privacy markers")

if __name__ == "__main__":
    main()
