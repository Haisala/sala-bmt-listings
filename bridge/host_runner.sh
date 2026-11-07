#!/bin/bash
set -euo pipefail

# Bridge host runner. Safe by default: no work occurs unless explicitly enabled.
BRIDGE_ENABLED="${BRIDGE_ENABLED:-0}"
if [[ "$BRIDGE_ENABLED" != "1" ]]; then
  printf '%s\n' "BRIDGE_DISABLED: set BRIDGE_ENABLED=1 to arm the host runner" >&2
  exit 0
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ROOT="${BRIDGE_RUNTIME_ROOT:-$REPO_ROOT/bridge_runtime}"
INBOX="$RUNTIME_ROOT/inbox"
PYTHON_BIN="${BRIDGE_PYTHON:-/usr/bin/python3}"

export BRIDGE_INBOX="$INBOX"
"$PYTHON_BIN" "$REPO_ROOT/bridge/fetch_latest_candidate.py"

exec "$PYTHON_BIN" "$REPO_ROOT/bridge/mvp_broker.py" \
  --inbox "$INBOX" \
  --once
