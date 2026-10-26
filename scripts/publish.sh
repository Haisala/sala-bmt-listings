#!/bin/sh
set -eu
ROOT=/Users/trandinhhieu/sala-bmt-listings
[ "$(git -C "$ROOT" rev-parse --show-toplevel)" = "$ROOT" ] || { echo 'FAIL CLOSED: wrong repository'; exit 1; }
[ "$(git -C "$ROOT" remote get-url origin | sed 's#/$##')" = 'https://github.com/Haisala/sala-bmt-listings.git' ] || { echo 'FAIL CLOSED: wrong origin'; exit 1; }
python3 "$ROOT/scripts/listing_manager.py" publish
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
RUN_ID=""
for _ in $(seq 1 30); do
  RUN_ID="$(gh run list --repo Haisala/sala-bmt-listings --workflow pages.yml --limit 10 --json databaseId,headSha --jq ".[] | select(.headSha == \"$HEAD_SHA\") | .databaseId" | head -n 1)"
  [ -n "$RUN_ID" ] && break
  sleep 2
done
[ -n "$RUN_ID" ] || { echo "FAIL: no Pages run for $HEAD_SHA"; exit 1; }
gh run watch "$RUN_ID" --repo Haisala/sala-bmt-listings --compact --exit-status
curl -fsS -o /dev/null "https://haisala.github.io/sala-bmt-listings/"
curl -fsS -o /dev/null "https://haisala.github.io/sala-bmt-listings/listings/nha-xanh-co-ban-cu-ebur/"
echo "PUBLISH VERIFY PASS"
