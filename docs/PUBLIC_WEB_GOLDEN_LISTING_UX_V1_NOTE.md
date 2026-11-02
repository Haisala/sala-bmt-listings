# Public Web Golden Listing UX V1

## Scope

The golden conversion flow is the real Cư Êbur listing `nha-xanh-co-ban-cu-ebur`. Canonical listing facts and the EcoCity `images: []` state were preserved.

## Implemented

- Direct Cư Êbur route now uses the current BMT Property Hub detail shell and relative GitHub Pages-safe assets.
- The above-fold detail flow shows status, title, location, asking price, key facts, gallery, call, Zalo, and local save action.
- Existing card/detail touch gallery and `1/N` indicator remain in use; detail thumbnails stay synchronized.
- Saved state remains local-only in `localStorage`; `?saved=1` provides a deterministic saved-list view.
- Share links use a URL-resolved `/listings/<slug>/` path, including from detail-related cards.
- The unsupported header menu and detail map controls were removed; unsupported media tabs are hidden rather than presented as active functionality.
- Related cards use real listing data only and are labeled `Có thể bạn cũng quan tâm`.

## Verification

- `node --check app.js`: PASS.
- `scripts/test_golden_listing.py`: PASS.
- Mobile gallery, EcoCity public, listing validation, Property Core, Intake Adapter, Matching, Review Admin, and 5B-N deterministic tests: PASS.
- P0-A/P0-C runtime-binding tests could not bind an ephemeral port in the sandbox; this is an environment limitation, not a Public Web assertion.
- Local port 8765 was unavailable during this run, so local HTTP runtime verification is NOT VERIFIED.

## Deployment / production

Deployment is intentionally recorded after the selected files are committed and Pages completes. Production verification must include homepage, direct Cư Êbur route, app/style/data assets, gallery contract fingerprint, EcoCity route, and public privacy scan.

## Limitations

Physical iPhone swipe feel and saved-state persistence across devices were not measured here. EcoCity still has no verified media and remains without a public gallery. Public Web remains static; Save is device-local.
