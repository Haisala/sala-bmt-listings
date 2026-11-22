"""Runtime-lock contracts for the established and isolated staging Admins."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from property_memory.runtime_lock import RuntimeLockError, validate_runtime_lock


class RuntimeLockTests(unittest.TestCase):
    def check_runtime(self, port: int, host: str = "127.0.0.1", environment: str = "staging"):
        folder = tempfile.TemporaryDirectory(prefix="issue39-runtime-lock-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        checkout = root / "release"
        checkout.mkdir()
        private = root / "private"
        private.mkdir(mode=0o700)
        database = private / "property-memory.sqlite3"
        manifest_path = private / "runtime-manifest.json"
        manifest = {
            "environment": environment,
            "instance_id": "TEST_ONLY_FIRST_WEB_ADMIN",
            "checkout": str(checkout),
            "revision": "TEST_ONLY_SHA",
            "host": host,
            "port": port,
            "database": str(database),
            "private_root": str(private),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        env = {
            "BMT_RUNTIME_LOCK": "1",
            "BMT_RUNTIME_MANIFEST": str(manifest_path),
            "BMT_ENV": environment,
            "BMT_CHECKOUT": str(checkout),
            "BMT_BACKEND_REVISION": "TEST_ONLY_SHA",
            "BMT_ADMIN_HOST": host,
            "BMT_ADMIN_PORT": str(port),
            "BMT_ADMIN_PRIVATE_ROOT": str(private),
            "BMT_ADMIN_DB_PATH": str(database),
        }
        return validate_runtime_lock(environ=env)

    def test_established_and_new_first_web_admin_ports_are_allowed(self):
        self.assertEqual(self.check_runtime(18876).port, 18876)
        self.assertEqual(self.check_runtime(18878).port, 18878)

    def test_customer_production_and_unbound_ports_are_rejected(self):
        for port in (8765, 18877, 18879, 8876):
            with self.subTest(port=port), self.assertRaisesRegex(RuntimeLockError, "STAGING_BINDING_REQUIRED"):
                self.check_runtime(port)

    def test_non_loopback_and_non_staging_are_rejected(self):
        with self.assertRaisesRegex(RuntimeLockError, "STAGING_BINDING_REQUIRED"):
            self.check_runtime(18878, host="0.0.0.0")
        with self.assertRaisesRegex(RuntimeLockError, "STAGING_BINDING_REQUIRED"):
            self.check_runtime(18878, environment="production")


if __name__ == "__main__":
    unittest.main(verbosity=2)
