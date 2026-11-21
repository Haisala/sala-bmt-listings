# Customer Web host preparation (P1.1)

This is an isolated macOS launchd template. It is deliberately **not installed or loaded**. It binds only `127.0.0.1:18877`, uses a private SQLite path outside the static frontend, and scrubs inherited process environment with `env -i`. It does not include or fetch an Owner credential. Until a service-specific approved secret injector is configured, Owner login and all write/review operations must remain unavailable; keep the public maintenance page in place.

## Candidate host facts

- Mac has Python 3.13 and `cloudflared` installed; port 18877 was free at preflight. This is a candidate, not proof of long-term uptime or a deployed service.
- `launchd` has no Customer Web job loaded. Existing Admin and other service labels/configurations were not changed.
- The current process shell had no Owner credential. A value was observable only as present in the global launchd environment; its source, project scope and suitability could not be proven, so this configuration intentionally does not inherit or use it.
- No tunnel, DNS, Pages setting, secret, database or customer data is created by these files.

## Stage-only setup outline

1. Install a reviewed release into `~/Library/Application Support/SALA Customer Web/current` (do not run from the dirty repository worktree).
2. Prepare isolated private folders:

   ```sh
   python3 deploy/runtime_dirs.py "$HOME/Library/Application Support/SALA Customer Web"
   ```

3. Place the plist at `~/Library/LaunchAgents/com.haisala.customer-web.plist` only after a service-specific secret provider and log directories are verified. `RunAtLoad` is false; do not bootstrap this template before host checks. Do not write an Owner password to the plist or a file.
4. Use a separate Customer Web ingress/tunnel only after local HTTP, restart, backup/restore and authentication checks pass. Do not reuse Admin staging or any other system tunnel. Keep apex/www on the maintenance page until the new host passes its gate.

## SQLite backup and restore

Use the standard-library SQLite online backup API so WAL data is included consistently. Backups/restores are non-overwriting by default, integrity/schema checked, atomically written, and restricted to mode `0600` inside an owner-only directory. Example paths must be private and outside static assets:

```sh
python3 deploy/storage_tools.py backup /private/path/customer.sqlite3 /private/path/backups/customer.sqlite3
python3 deploy/storage_tools.py restore /private/path/backups/customer.sqlite3 /private/path/restore/customer.sqlite3
```

Tests use only temporary synthetic TEST_ONLY rows and never reference the production domain or live store.
