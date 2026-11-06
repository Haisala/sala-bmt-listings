import copy, hashlib, json, unittest
from datetime import datetime, timezone, timedelta
from c9d_consumer import consume

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)

def record():
    r = {"candidate_id": "c9d-15", "repository": "Haisala/sala-bmt-listings",
         "issue_number": 15, "issue_url": "https://github.com/Haisala/sala-bmt-listings/issues/15",
         "event_name": "issues.opened", "event_id": "synthetic-run-15", "task_digest": "",
         "nonce": "nonce-15", "created_at": "2030-01-01T00:00:00Z",
         "expiry": "2030-01-01T01:00:00Z", "status": "CANDIDATE_ONLY"}
    material = {k: r[k] for k in ("repository", "issue_number", "issue_url", "event_name")}
    r["task_digest"] = "sha256:" + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return r

class C9DTests(unittest.TestCase):
    def check_reject(self, r, reason, **kw):
        out = consume(r, now=NOW, **kw)
        self.assertEqual(out["result"], "REJECTED")
        self.assertEqual(out["reason"], reason)
        self.assertEqual(out["EXECUTION_CALLS"], 0)

    def test_valid_synthetic_issue_seen(self):
        self.assertEqual(consume(record(), now=NOW), {"result": "SEEN", "reason": "candidate_valid", "EXECUTION_CALLS": 0})
    def test_wrong_repository(self):
        r=record(); r["repository"]="other/repo"; self.check_reject(r, "repository_not_allowlisted")
    def test_wrong_title_represented_by_missing_upstream_candidate(self):
        r=record(); r["status"]="REJECTED"; self.check_reject(r, "invalid_status")
    def test_missing_schema(self):
        r=record(); del r["nonce"]; self.check_reject(r, "malformed_record")
    def test_expired(self):
        r=record(); r["expiry"]="2029-12-31T23:59:00Z"; self.check_reject(r, "expired")
    def test_nonce_duplicate(self):
        self.check_reject(record(), "duplicate_nonce", used_nonces={"nonce-15"})
    def test_digest_mismatch(self):
        r=record(); r["task_digest"]="sha256:wrong"; self.check_reject(r, "digest_mismatch")
    def test_duplicate_candidate(self):
        self.check_reject(record(), "duplicate_candidate", seen_ids={"c9d-15"})
    def test_malformed_record(self):
        self.check_reject({"candidate_id":"x"}, "malformed_record")
    def test_execution_calls_zero_for_rejection(self):
        r=record(); r["status"]="RUNNING"; self.check_reject(r, "invalid_status")

if __name__ == "__main__": unittest.main()
