#!/usr/bin/env python3
"""C9D candidate-only read-only consumer.

Reads one JSON candidate and returns SEEN or REJECTED. It has no execution
adapter and deliberately exposes EXECUTION_CALLS=0 in its output.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone

REPOSITORY = "Haisala/sala-bmt-listings"
REQUIRED = {"candidate_id", "repository", "issue_number", "issue_url", "event_name",
            "event_id", "task_digest", "nonce", "created_at", "expiry", "status"}

def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def expected_digest(record):
    material = {"repository": record["repository"], "issue_number": record["issue_number"],
                "issue_url": record["issue_url"], "event_name": record["event_name"]}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

def consume(record, now=None, used_nonces=None, seen_ids=None):
    used_nonces = used_nonces or set()
    seen_ids = seen_ids or set()
    if not isinstance(record, dict) or set(record) != REQUIRED:
        return {"result": "REJECTED", "reason": "malformed_record", "EXECUTION_CALLS": 0}
    checks = [
        (record["repository"] == REPOSITORY, "repository_not_allowlisted"),
        (record["status"] == "CANDIDATE_ONLY", "invalid_status"),
        (record["event_name"] == "issues.opened", "invalid_event"),
        (record["task_digest"] == expected_digest(record), "digest_mismatch"),
        (record["candidate_id"] not in seen_ids, "duplicate_candidate"),
        (record["nonce"] not in used_nonces, "duplicate_nonce"),
    ]
    for ok, reason in checks:
        if not ok:
            return {"result": "REJECTED", "reason": reason, "EXECUTION_CALLS": 0}
    try:
        current = now or datetime.now(timezone.utc)
        if parse_time(record["expiry"]) <= current:
            return {"result": "REJECTED", "reason": "expired", "EXECUTION_CALLS": 0}
        parse_time(record["created_at"])
    except Exception:
        return {"result": "REJECTED", "reason": "invalid_time", "EXECUTION_CALLS": 0}
    return {"result": "SEEN", "reason": "candidate_valid", "EXECUTION_CALLS": 0}

def main():
    if len(sys.argv) != 2:
        print(json.dumps({"result": "REJECTED", "reason": "usage", "EXECUTION_CALLS": 0}))
        return 2
    with open(sys.argv[1], encoding="utf-8") as handle:
        record = json.load(handle)
    print(json.dumps(consume(record), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
