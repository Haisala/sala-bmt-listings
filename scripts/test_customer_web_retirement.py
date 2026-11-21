#!/usr/bin/env python3
"""Read-only check that the old static Customer Web is retired."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
legacy = [
    "index.html", "listing.html", "app.js", "campaign.js", "styles.css", "robots.txt", "sitemap.xml",
    "listings/can-ho-ecocity-ton-duc-thang-6-trieu/index.html",
    "listings/can-ho-ecocity-ton-duc-thang-7-trieu/index.html",
    "listings/nha-xanh-co-ban-cu-ebur/index.html",
]
remaining = [path for path in legacy if (ROOT / path).exists()]
assert not remaining, f"Legacy Customer Web files still exist: {remaining}"
for required in ("customer_web_v2/sala_web.py", "customer_web_v2/maintenance/index.html", "customer_web_v2/frontend/index.html", ".github/workflows/pages.yml"):
    assert (ROOT / required).is_file(), f"Required replacement file missing: {required}"
workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
assert "path: ./customer_web_v2/maintenance" in workflow
assert "path: .\n" not in workflow
# Shared Admin/Property Core data and media remain untouched.
assert (ROOT / "data/listings.json").is_file()
assert (ROOT / "assets/images").is_dir()
print("LEGACY RETIREMENT PASS: old static UI absent; Customer Web core/maintenance present; Pages allowlisted; shared data/media retained")
