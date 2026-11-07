#!/usr/bin/env python3
"""Download the newest successful Bridge candidate artifact into an inbox.

The caller must explicitly arm the host runner with BRIDGE_ENABLED=1.
No GitHub token is read from the environment; gh uses its normal host credential.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> int:
    repository = os.environ.get("BRIDGE_REPOSITORY", "Haisala/sala-bmt-listings")
    workflow = os.environ.get("BRIDGE_WORKFLOW", "bridge-mvp-issue-event.yml")
    inbox = pathlib.Path(os.environ.get("BRIDGE_INBOX", "bridge_runtime/inbox"))
    inbox.mkdir(parents=True, exist_ok=True)

    runs_json = run(
        "gh", "run", "list", "--repo", repository,
        "--workflow", workflow, "--event", "issues",
        "--status", "success", "--limit", "10",
        "--json", "databaseId,createdAt",
    )
    runs = json.loads(runs_json or "[]")
    if not runs:
        print(json.dumps({"status": "NO_CANDIDATE", "reason": "no_successful_bridge_run"}))
        return 0

    run_id = str(runs[0]["databaseId"])
    staging = inbox / ".artifact-download"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    subprocess.run(
        "gh", "run", "download", run_id, "--repo", repository,
        "--dir", str(staging), check=True,
    )
    print(json.dumps({"status": "ARTIFACT_FETCHED", "workflow_run": run_id, "inbox": str(inbox)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
