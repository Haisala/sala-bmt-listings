import base64
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from customer_web_v2.deploy.snapshot_bundle import create_snapshot, restore_snapshot


class SnapshotBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        self.db_path = self.source / "property-core.sqlite3"
        db = sqlite3.connect(self.db_path)
        db.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
        db.execute("INSERT INTO schema_meta VALUES(7)")
        db.execute("CREATE TABLE properties(property_id TEXT PRIMARY KEY, title TEXT NOT NULL)")
        db.execute("INSERT INTO properties VALUES('TEST_ONLY-property-1','TEST_ONLY fixture')")
        db.commit(); db.close(); self.db_path.chmod(0o600)
        self.media = self.source / "media" / "TEST_ONLY-front.png"
        self.media.parent.mkdir(mode=0o700)
        self.media.write_bytes(b"TEST_ONLY synthetic media bytes")
        self.media.chmod(0o600)
        self.backups = self.root / "backups"
        self.backups.mkdir(mode=0o700)
        self.passphrase = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=")

    def tearDown(self):
        self.temp.cleanup()

    def test_encrypted_data_media_snapshot_manifest_and_restore(self):
        snapshot = self.backups / "snapshot.sala.enc"
        receipt = create_snapshot(self.source, ["property-core.sqlite3", "media/TEST_ONLY-front.png"],
                                  "property-core.sqlite3", snapshot, self.passphrase)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        raw = snapshot.read_bytes()
        self.assertTrue(raw.startswith(b"SALA-BDS-SNAPSHOT-V1\n"))
        self.assertNotIn(b"TEST_ONLY fixture", raw)
        self.assertEqual(receipt["file_count"], 2)
        self.assertEqual(receipt["table_counts"]["properties"], 1)

        restored = self.root / "restored"
        result = restore_snapshot(snapshot, restored, self.passphrase)
        self.assertEqual(result["manifest_sha256"], receipt["manifest_sha256"])
        self.assertEqual((restored / "media" / "TEST_ONLY-front.png").read_bytes(), self.media.read_bytes())
        db = sqlite3.connect(restored / "property-core.sqlite3")
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(db.execute("SELECT version FROM schema_meta").fetchone()[0], 7)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 1)
        db.close()
        self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((restored / "property-core.sqlite3").stat().st_mode), 0o600)

    def test_wrong_key_tamper_and_overwrite_fail_closed(self):
        snapshot = self.backups / "snapshot.sala.enc"
        create_snapshot(self.source, ["property-core.sqlite3", "media/TEST_ONLY-front.png"],
                        "property-core.sqlite3", snapshot, self.passphrase)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_AUTHENTICATION_FAILED"):
            restore_snapshot(snapshot, self.root / "wrong-key", base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"="))
        tampered = self.backups / "tampered.sala.enc"
        altered = bytearray(snapshot.read_bytes()); altered[-1] ^= 1
        tampered.write_bytes(altered); tampered.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_AUTHENTICATION_FAILED"):
            restore_snapshot(tampered, self.root / "tampered-target", self.passphrase)
        existing = self.root / "existing"
        existing.mkdir(mode=0o700)
        marker = existing / "preserve.txt"; marker.write_text("TEST_ONLY keep")
        with self.assertRaises(FileExistsError):
            restore_snapshot(snapshot, existing, self.passphrase)
        self.assertEqual(marker.read_text(), "TEST_ONLY keep")

    def test_refuses_insecure_source_root_and_does_not_chmod_target_parent(self):
        public_source = self.root / "public-source"; public_source.mkdir(mode=0o755); public_source.chmod(0o755)
        unsafe_out = self.root / "unsafe-out"; unsafe_out.mkdir(mode=0o755); unsafe_out.chmod(0o755)
        with self.assertRaisesRegex(PermissionError, "SNAPSHOT_ROOT_NOT_PRIVATE"):
            create_snapshot(public_source, ["property-core.sqlite3"], "property-core.sqlite3",
                            unsafe_out / "blocked.enc", self.passphrase)
        with self.assertRaisesRegex(PermissionError, "SNAPSHOT_ROOT_NOT_PRIVATE"):
            create_snapshot(self.source, ["property-core.sqlite3"], "property-core.sqlite3",
                            unsafe_out / "blocked.enc", self.passphrase)
        self.assertEqual(stat.S_IMODE(unsafe_out.stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
