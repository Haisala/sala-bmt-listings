# SALA BĐS Customer Web v2 (P0.9)

Independent customer-facing application, implemented with Python's standard library, SQLite and a small mobile-first browser UI. It starts with an empty store: no existing listings, real estate data, media, customer database, or Property Core records are imported.

## Run locally

Python 3.11+ is sufficient. Set an Owner password through the machine's approved secret manager or protected environment injection; the app refuses weak bootstrap passwords and never prints the supplied value. The SQLite database defaults to `customer_web_v2/var/customer_web.sqlite3`, is outside the static asset map, and is created with mode `0600`.

```sh
# Inject SALA_OWNER_PASSWORD securely (never paste it into chat or logs).
export SALA_ALLOWED_ORIGINS='http://127.0.0.1:18877'
export SALA_DB_PATH='/absolute/private/path/customer-web.sqlite3'
export SALA_PORT=18877
python3 customer_web_v2/sala_web.py
```

The server binds only to loopback and refuses `0.0.0.0`, port `8765`, and port `18876`. For an actual deployment, terminate HTTPS at the hosting edge, set `SALA_ALLOWED_ORIGINS` to the exact HTTPS origin, inject the Owner password securely, place the database on durable private storage, and provide backup/restore and process supervision. The current repository checkout does not configure or perform that deployment.

## Data flow and safeguards

```text
Owner login → authenticated multipart intake → validate / normalize / deduplicate
            → private SQLite Property + Listing + Revision + Media + Provenance
            → Owner approve/reject → approved-only public search/detail/media
```

- `property_id` is separate from `listing_id`; a property may have one sale and one rental listing. Each edit creates a new `revision_id` and returns the listing to `PENDING_REVIEW` until approved again.
- Intake, review, edit, and media endpoints require a valid server-side Owner session and an exact allowed `Origin`. The password is PBKDF2-HMAC-SHA256 hashed; session tokens are random, stored as hashes, expire after eight hours, and are in HttpOnly/Secure/SameSite=Strict cookies. Login is rate-limited in process.
- Only approved listings and their current revision media are returned publicly. There are no real phone/email links or external CTA. The UI uses DOM text nodes, not HTML interpolation.
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

`CODE_BUILT` and local/in-process tests are recorded in `release-manifest.json`. Live HTTP, physical iPhone/browser verification, production hosting, secret injection, durable-host backup/restore, and apex cutover require a host environment and release checks that have not been performed yet. Do not point `haisala.io.vn` at this application based on in-process tests alone.
