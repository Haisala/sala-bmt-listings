#!/usr/bin/env python3
"""Isolated tests for the privacy-limited Website analytics store."""
import os
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from property_memory.analytics_store import AnalyticsStore, AnalyticsValidationError


PROPERTY_ID = "property-0123456789abcdef"


def event(event_type="page_view", **extra):
    value = {"event_id": str(uuid.uuid4()), "event_type": event_type}
    value.update(extra)
    if event_type == "page_view" and "page_type" not in value:
        value["page_type"] = "home"
    return value


class AnalyticsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AnalyticsStore(Path(self.temp.name) / "private" / "analytics.sqlite3", retention_days=3)

    def tearDown(self):
        self.temp.cleanup()

    def test_ingest_idempotency_and_owner_filters(self):
        first = event("property_detail_view", property_id=PROPERTY_ID)
        self.assertFalse(self.store.ingest(first, property_is_public=lambda _: True)["duplicate"])
        self.assertTrue(self.store.ingest(first, property_is_public=lambda _: True)["duplicate"])
        utc_today = datetime.now(timezone.utc).date()
        result = self.store.summary(start_date=utc_today, end_date=utc_today, property_id=PROPERTY_ID)
        self.assertEqual(result["total_events"], 1)
        self.assertEqual(result["by_property"], [{"property_id": PROPERTY_ID, "event_type": "property_detail_view", "count": 1}])
        self.assertFalse(result["privacy"]["phone_lead_inferred"])

    def test_conflicting_replay_rejected_and_other_property_unaffected(self):
        event_id = str(uuid.uuid4())
        self.store.ingest(event("page_view", event_id=event_id))
        with self.assertRaisesRegex(AnalyticsValidationError, "ANALYTICS_EVENT_ID_CONFLICT"):
            self.store.ingest(event("contact_cta_click", event_id=event_id, channel="call"))
        utc_today = datetime.now(timezone.utc).date()
        result = self.store.summary(start_date=utc_today, end_date=utc_today)
        self.assertEqual(result["total_events"], 1)

    def test_rejects_unknown_private_fields_and_unknown_property(self):
        with self.assertRaisesRegex(AnalyticsValidationError, "ANALYTICS_PAYLOAD_KEYS_REJECTED"):
            self.store.ingest(event(phone="0901234567"))
        with self.assertRaisesRegex(AnalyticsValidationError, "ANALYTICS_PROPERTY_NOT_PUBLIC"):
            self.store.ingest(event("property_detail_view", property_id=PROPERTY_ID), property_is_public=lambda _: False)
        with self.assertRaisesRegex(AnalyticsValidationError, "ANALYTICS_UTM_CAMPAIGN_INVALID"):
            self.store.ingest(event(utm_campaign="090-123-4567"))

    def test_rejects_malformed_unhashable_allowlist_values(self):
        for payload in (
            event(event_type=[]),
            event("page_view", page_type={}),
            event("contact_cta_click", channel=[]),
            event("search_filter", filter_type=[], filter_value=[]),
            event(utm_source=[]),
            event(utm_medium={}),
        ):
            with self.subTest(payload=payload), self.assertRaises(AnalyticsValidationError):
                self.store.ingest(payload)

    def test_refuses_non_private_directory_and_payload_over_limit(self):
        public_dir = Path(self.temp.name) / "public"
        public_dir.mkdir(mode=0o755)
        public_dir.chmod(0o755)
        with self.assertRaisesRegex(OSError, "ANALYTICS_STORAGE_NOT_PRIVATE"):
            AnalyticsStore(public_dir / "events.sqlite3").ingest(event())
        with self.assertRaisesRegex(AnalyticsValidationError, "ANALYTICS_PAYLOAD_TOO_LARGE"):
            AnalyticsStore(Path(self.temp.name) / "other" / "events.sqlite3", max_bytes=32).ingest(event())

    def test_retention_purges_expired_events(self):
        old = datetime.now(timezone.utc) - timedelta(days=5)
        with patch("property_memory.analytics_store._now", return_value=old):
            self.store.ingest(event())
        with patch("property_memory.analytics_store._now", return_value=datetime.now(timezone.utc)):
            result = self.store.summary(start_date=date.today() - timedelta(days=2), end_date=date.today())
        self.assertEqual(result["total_events"], 0)

    def test_database_file_is_private(self):
        self.store.ingest(event())
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
