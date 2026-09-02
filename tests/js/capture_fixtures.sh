#!/usr/bin/env bash
# Capture live API responses for the jsdom UI harness.
#
#   ./tests/js/capture_fixtures.sh [base_url] [out_dir]
#
# Defaults: http://127.0.0.1:8765 and /tmp/fixtures. Run against a server that
# has demo data (python -m leadgen demo) so the tables are not empty.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8765}/api"
OUT="${2:-/tmp/fixtures}"
mkdir -p "$OUT"

CID="$(curl -s "$BASE/campaigns" | python3 -c 'import sys,json; c=json.load(sys.stdin)["campaigns"]; print(c[0]["id"] if c else 1)')"
echo "using campaign id $CID"

get() { curl -sf "$BASE/$2" -o "$OUT/$1.json" && printf '  %-16s %s bytes\n' "$1" "$(stat -c%s "$OUT/$1.json")"; }

get ping              "ping"
get health            "system/health"
get posture           "system/compliance-posture"
get settings          "system/settings"
get accounts          "accounts"
get countries         "targeting/countries"
get archetypes        "targeting/niche-archetypes"
get overview          "crm/overview"
get replies           "crm/replies?limit=40"
get pipeline          "crm/pipeline"
get suppressions      "crm/suppressions"
get campaigns         "campaigns"
get dispatch_state    "campaigns/dispatch/state"
get campaign_detail   "campaigns/$CID"
get leads             "campaigns/$CID/leads?limit=300"

echo "wrote $(ls -1 "$OUT" | wc -l) fixtures to $OUT"
