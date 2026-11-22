# Customer Web host preparation (Issue #39)

This candidate is source/build preparation only. The launchd example is not installed or loaded, and no process, tunnel, DNS, Pages setting, or production service was changed. The Customer service entrypoint is projection-only: it does not initialize the legacy Customer SQLite store, provision Customer Owner credentials, or expose Customer Owner/intake/review routes. SALA Admin remains the Owner authority; Customer reads the configured approved projection and returns 503 when that source is absent or invalid.

## Runtime configuration still required

The example plist binds loopback and scrubs inherited environment, but intentionally omits unverified paths. Before any separately authorized staging start, the release operator must configure and verify:

- `SALA_PUBLIC_PROJECTION_PATH`: the exact Admin-approved projection artifact path.
- `SALA_PUBLIC_ASSETS_ROOT`: the matching approved public media root.
- `SALA_ADMIN_ANALYTICS_URL`: the trusted Admin analytics endpoint; absent configuration causes event relay to fail closed with 503.
- The exact Customer origin must be permitted by Admin analytics origin policy; property events are bound to active `property_id + publication_id`.

No Owner password or Customer database path belongs in the public service configuration. The current plist example is not a ready-to-start or public deployment artifact because the exact projection/media/analytics paths and ingress are not configured.

## Allowlisted release bundle

`build_release_bundle.py` packages only `release-allowlist.txt` runtime files, emits a deterministic SHA-256 manifest, and never deploys or changes GitHub Pages. Build output must be in a private directory outside the source checkout:

```sh
umask 077
python3 deploy/build_release_bundle.py /private/path/private-release-dir --source-revision <reviewed-git-sha>
```

The bundle is a service bundle, not proof of a configured Customer host or Pages artifact. No workflow was changed; the repository's competing historical Pages configuration remains a release blocker pending authoritative workflow/artifact verification.

## TEST_ONLY snapshot utility

`snapshot_bundle.py` offers `create_snapshot` and `restore_snapshot` for explicitly selected private SQLite and media paths. It takes a consistent SQLite online backup, records table counts/schema and file hashes, encrypts using OpenSSL AES-256-CBC/PBKDF2, and authenticates the archive with HMAC before restore. It never chooses real paths or stores the passphrase. Tests use only temporary TEST_ONLY data and a synthetic image, verifying tamper/wrong-key rejection, private permissions and non-overwrite behavior. No real-data inventory, backup, cleanup, or restore was performed.

## Release gates

The isolated HTTP harness proves Admin projection → Customer public reads, analytics receipts, revocation behavior and disabled duplicate Customer Owner APIs on temporary TEST_ONLY roots. It does not prove the current launchd service, HTTPS/browser/iPhone behavior, staging/production routing, live Content API availability, or Owner acceptance. Do not install, start, route, merge, or deploy from this candidate without the exact source/artifact and operation gate required by Issue #39.
