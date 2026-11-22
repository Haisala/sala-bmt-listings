import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from deploy.storage_tools import backup, restore
from sala_web import Application
from test_customer_web import ORIGIN, PASSWORD, listing_fields, multipart


class StorageToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "private" / "customer.sqlite3"
        self.app = Application(self.source, PASSWORD, {ORIGIN})
        content_type, body = multipart(listing_fields())
        response = self.app.handle("POST", "/api/intakes", {"Origin": ORIGIN, "Content-Type": content_type}, body)
        self.assertEqual(response.status, 401)
        # Populate only a TEST_ONLY store using the explicit synthetic fixture.
        self.app = Application(self.source, PASSWORD, {ORIGIN})
        from test_customer_web import SimpleCookie
        login = self.app.handle("POST", "/api/login", {"Origin": ORIGIN, "Content-Type": "application/json"}, json.dumps({"password": PASSWORD}).encode())
        jar = SimpleCookie(); jar.load(login.headers["Set-Cookie"])
        content_type, body = multipart(listing_fields())
        headers = {"Origin": ORIGIN, "Content-Type": content_type, "Cookie": f"sala_owner_session={jar['sala_owner_session'].value}"}
        created = self.app.handle("POST", "/api/intakes", headers, body)
        self.listing_id = json.loads(created.body)["listing_id"]
        approved = self.app.handle("POST", f"/api/listings/{self.listing_id}/review", {"Origin": ORIGIN, "Content-Type": "application/json", "Cookie": headers["Cookie"]}, b'{"action":"approve"}')
        self.assertEqual(approved.status, 200)

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_restore_preserves_synthetic_approved_record_privately(self):
        snapshot = self.root / "backups" / "test.sqlite3"
        restored = self.root / "restore" / "customer.sqlite3"
        backup(self.source, snapshot)
        restore(snapshot, restored)
        recovered = Application(restored, PASSWORD, {ORIGIN})
        items = recovered.store.list_all()
        self.assertEqual([x["listing_id"] for x in items], [self.listing_id])
        # A legacy-approved row is restored intact but must not become public
        # without the separately configured Admin projection.
        self.assertEqual(recovered.handle("GET", "/api/listings?q=TEST_ONLY").status, 503)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restored.stat().st_mode & 0o777, 0o600)
        self.assertEqual(snapshot.parent.stat().st_mode & 0o777, 0o700)

    def test_corrupt_backup_rejected_without_creating_destination(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not sqlite")
        destination = self.root / "restore" / "bad.sqlite3"
        with self.assertRaises(Exception):
            restore(corrupt, destination)
        self.assertFalse(destination.exists())

    def test_existing_destination_is_never_overwritten(self):
        snapshot = self.root / "backups" / "test.sqlite3"
        backup(self.source, snapshot)
        destination = self.root / "restore" / "already.sqlite3"
        destination.parent.mkdir(mode=0o700)
        destination.write_bytes(b"preserve-me")
        with self.assertRaises(FileExistsError):
            restore(snapshot, destination)
        self.assertEqual(destination.read_bytes(), b"preserve-me")


if __name__ == "__main__":
    unittest.main()
