#!/usr/bin/env python3
"""Deterministic source/data contract checks for the lightweight mobile gallery."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
js = (root / "app.js").read_text(encoding="utf-8")
data = json.loads((root / "data" / "listings.json").read_text(encoding="utf-8"))
by_slug = {item["slug"]: item for item in data}
cuebur = by_slug["nha-xanh-co-ban-cu-ebur"]
ecocity = by_slug["can-ho-ecocity-ton-duc-thang-7-trieu"]

assert len(cuebur["images"]) == 10
assert ecocity["images"] == []
for marker in ("gallery-swipe", "touchstart", "touchmove", "touchend", "gallery-indicator", "data-thumb"):
    assert marker in js, marker
assert "touch-action:pan-y" in (root / "styles.css").read_text(encoding="utf-8")
assert "images[0]" not in js.split("function card", 1)[1].split("function render", 1)[0]
print("MOBILE GALLERY PASS: Cư Êbur 10 ảnh, card/detail touch contract, indicator, thumbnail sync, EcoCity unchanged")
