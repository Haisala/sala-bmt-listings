# Customer Web host preparation (P1.1)

This is an isolated macOS launchd template. It is deliberately **not installed or loaded**. It binds only `127.0.0.1:18877`, uses a private SQLite path outside the static frontend, and scrubs inherited process environment with `env -i`. It points to the existing SALA BĐS Owner password source at `/Users/trandinhhieu/BMTPropertyHubPrivate/admin-password`; the file itself is neither copied nor printed. The app validates its owner-only permissions and stores only a salted PBKDF2 verifier in its own private SQLite database. A service restart reloads the current source value.

## Candidate host facts

- Mac has Python 3.13 and `cloudflared` installed; port 18877 was free at preflight. This is a candidate, not proof of long-term uptime or a deployed service.
- `launchd` has no Customer Web job loaded. Existing Admin and other service labels/configurations were not changed.
- Existing source metadata was inspected without reading the file: Owner-owned regular file, mode `0600`, parent directory mode `0700`. The Customer Web service uses this explicit path only; it does not inherit the ambiguous global launchd variable.
- No tunnel, DNS, Pages setting, secret, database or customer data is created by these files.

## Stage-only setup outline

1. Install a reviewed release into `~/Library/Application Support/SALA Customer Web/current` (do not run from the dirty repository worktree).
2. Prepare isolated private folders:

   ```sh
   python3 deploy/runtime_dirs.py "$HOME/Library/Application Support/SALA Customer Web"
   ```

3. Place the plist at `~/Library/LaunchAgents/com.haisala.customer-web.plist` only after the release and private runtime/log directories are verified. The plist contains only the path to the pre-existing Owner source, not its contents. `RunAtLoad` is false; bootstrap only after isolated host checks. Do not copy or print the Owner password.
4. Use a separate Customer Web ingress/tunnel only after local HTTP, restart, backup/restore and authentication checks pass. Do not reuse Admin staging or any other system tunnel. Keep apex/www on the maintenance page until the new host passes its gate.

## SQLite backup and restore

Use the standard-library SQLite online backup API so WAL data is included consistently. Backups/restores are non-overwriting by default, integrity/schema checked, atomically written, and restricted to mode `0600` inside an owner-only directory. Example paths must be private and outside static assets:

```sh
python3 deploy/storage_tools.py backup /private/path/customer.sqlite3 /private/path/backups/customer.sqlite3
python3 deploy/storage_tools.py restore /private/path/backups/customer.sqlite3 /private/path/restore/customer.sqlite3
```

Tests use only temporary synthetic TEST_ONLY rows and never reference the production domain or live store.

The legacy password source is loaded only when the process starts and hashed in the Customer Web database. Password-source changes take effect after a controlled Customer Web restart; existing sessions remain valid until normal expiry or logout. No Admin password, account, service, or staging configuration is changed.
