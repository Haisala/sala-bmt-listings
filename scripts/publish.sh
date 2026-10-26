#!/bin/sh
set -eu
ROOT=/Users/trandinhhieu/sala-bmt-listings
[ "$(git -C "$ROOT" rev-parse --show-toplevel)" = "$ROOT" ] || { echo 'FAIL CLOSED: wrong repository'; exit 1; }
[ "$(git -C "$ROOT" remote get-url origin | sed 's#/$##')" = 'https://github.com/Haisala/sala-bmt-listings.git' ] || { echo 'FAIL CLOSED: wrong origin'; exit 1; }
python3 "$ROOT/scripts/listing_manager.py" publish
RUN_ID="$(gh run list --repo Haisala/sala-bmt-listings --workflow pages.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID" --repo Haisala/sala-bmt-listings --compact --exit-status
curl -fsS -o /dev/null "https://haisala.github.io/sala-bmt-listings/"
curl -fsS -o /dev/null "https://haisala.github.io/sala-bmt-listings/listings/nha-xanh-co-ban-cu-ebur/"
echo "PUBLISH VERIFY PASS"
