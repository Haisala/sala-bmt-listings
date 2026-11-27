import tempfile
import unittest
from pathlib import Path

from property_memory.store import PropertyStore


class PropertyPurgeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PropertyStore(Path(self.tmp.name) / "store.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def make_property(self, test_only=True):
        prop = self.store.create({"title": "synthetic", "lifecycle_state": "NEEDS_REVIEW"})
        self.store.create_source_appearance(prop["property_id"], {
            "source_id": "TEST_ONLY" if test_only else "PERSONAL",
            "source_group": "synthetic" if test_only else "real",
            "intake_id": "intake-test" if test_only else "intake-real",
            "rights_status": "UNKNOWN",
        })
        return prop["property_id"]

    def test_approved_synthetic_property_purge(self):
        property_id = self.make_property()
        self.assertEqual(self.store.purge_test_only_property(property_id, owner_intent="APPROVED_TEST_ONLY_PURGE", environment="staging"), "PURGED")
        self.assertIsNone(self.store.get(property_id))

    def test_real_record_rejected(self):
        property_id = self.make_property(test_only=False)
        with self.assertRaisesRegex(ValueError, "TEST_ONLY_PROVENANCE_REQUIRED"):
            self.store.purge_test_only_property(property_id, owner_intent="APPROVED_TEST_ONLY_PURGE", environment="staging")

    def test_missing_marker_rejected(self):
        property_id = self.store.create({"title": "unmarked", "lifecycle_state": "NEEDS_REVIEW"})["property_id"]
        with self.assertRaisesRegex(ValueError, "TEST_ONLY_PROVENANCE_REQUIRED"):
            self.store.purge_test_only_property(property_id, owner_intent="APPROVED_TEST_ONLY_PURGE", environment="staging")

    def test_wrong_environment_rejected(self):
        property_id = self.make_property()
        with self.assertRaisesRegex(ValueError, "WRONG_ENVIRONMENT"):
            self.store.purge_test_only_property(property_id, owner_intent="APPROVED_TEST_ONLY_PURGE", environment="production")

    def test_repeated_purge_is_idempotent(self):
        property_id = self.make_property()
        args = {"owner_intent": "APPROVED_TEST_ONLY_PURGE", "environment": "staging"}
        self.assertEqual(self.store.purge_test_only_property(property_id, **args), "PURGED")
        self.assertEqual(self.store.purge_test_only_property(property_id, **args), "ALREADY_ABSENT")


if __name__ == "__main__":
    unittest.main()
