# SALA BĐS Customer Web v2 (P0.9)

Customer-facing application, implemented with Python's standard library, SQLite and a small mobile-first browser UI. Its legacy private intake/review store remains isolated; it is not the public listing authority.

## Run locally

Python 3.11+ is sufficient. Set an Owner password through the machine's approved secret manager or protected environment injection; the app refuses weak bootstrap passwords and never prints the supplied value. The SQLite database defaults to `customer_web_v2/var/customer_web.sqlite3`, is outside the static asset map, and is created with mode `0600`.

```sh
# Inject SALA_OWNER_PASSWORD securely (never paste it into chat or logs).
export SALA_ALLOWED_ORIGINS='http://127.0.0.1:18877'
export SALA_DB_PATH='/absolute/private/path/customer-web.sqlite3'
export SALA_PORT=18877
# Point only at an Admin-produced, revision-bound public projection artifact.
export SALA_PUBLIC_PROJECTION_PATH='/absolute/path/to/admin-preview/data/listings.json'
export SALA_PUBLIC_ASSETS_ROOT='/absolute/path/to/admin-approved-assets-root'
python3 customer_web_v2/sala_web.py
```

The server binds only to loopback and refuses `0.0.0.0`, port `8765`, and port `18876`. For an actual deployment, terminate HTTPS at the hosting edge, set `SALA_ALLOWED_ORIGINS` to the exact HTTPS origin, inject the Owner password securely, place the database on durable private storage, and provide backup/restore and process supervision. The current repository checkout does not configure or perform that deployment.

## Data flow and safeguards

```text
Admin Gate A → canonical Property Core → separate Owner Web-publication approval
            → allowlisted, revision-bound public projection → Customer feed/search/detail/media

Legacy Customer Web Owner intake/review → private SQLite (retained; not a public source)
```

- Public feed, search, detail and media read only the artifact at `SALA_PUBLIC_PROJECTION_PATH`; no projection configuration, an unavailable/invalid artifact, or unsafe media fails closed. Public handlers never fall back to Customer Web SQLite. Admin Gate A and the separate Web publication decision are both represented by the source artifact's approval/revision lineage.
- `SALA_PUBLIC_ASSETS_ROOT` is optional; image paths must resolve under the configured root and the projection's slug folder, with approved media rights. Missing/unsafe media is omitted or returns not found.
- The legacy private intake/review/edit endpoints and Owner session remain for preservation and compatibility; their records and local `APPROVED` state do not grant public visibility. They are deprecated candidates pending a separate Owner-approved preservation/cutover plan. This increment does not create SSO or share credentials.
- No real phone/email links or external CTA are enabled. The UI uses DOM text nodes, not HTML interpolation.
- Uploads are capped at 8 images, 4 MiB per image and 10 MiB per request; only JPEG/PNG/WebP with matching magic bytes are accepted. Media is private in SQLite and served only for approved listings.
- SQLite schema version 1 and foreign-key constraints are initialized on startup. Pending input, rejection, approval, revisions, and media provenance persist across process restarts.
- The app is marked `noindex`; security headers include CSP, frame denial and `nosniff`. No sample listing is seeded into a normal database.

## Tests

Run isolated synthetic tests (they create and delete only temporary SQLite databases):

```sh
python3 -m unittest discover -s customer_web_v2/tests -v
node --check customer_web_v2/frontend/app.js
python3 -m py_compile customer_web_v2/sala_web.py customer_web_v2/tests/test_customer_web.py
```

The tests do not call external APIs, contact customers, use real property data, deploy, or write to the live Customer Web/Property Core.

## Current release state

The projection reader is source-only and tested against temporary synthetic Admin artifacts in PR #44. Runtime environment variables are not configured by this change. Live HTTP, physical iPhone/browser verification, production hosting, secret injection, durable-host backup/restore, and apex cutover have not been performed. Do not point `haisala.io.vn` at this application based on in-process tests alone.
