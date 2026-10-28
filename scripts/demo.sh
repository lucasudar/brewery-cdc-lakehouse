#!/usr/bin/env bash
# End-to-end demo: apply scripts/simulate_changes.sql to Postgres and watch every change reach the API.
# Needs only docker; assertions run inside the api container. Safe to run repeatedly.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
check() { compose exec -T api python - "$@" < scripts/demo_check.py; }

echo "==> Waiting for the pipeline to be ready (API + gold mart)"
for _ in $(seq 1 90); do
  if compose exec -T api python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/v1/metrics/daily-style-consumption?limit=1', timeout=2)" 2>/dev/null; then
    ready=1; break
  fi
  sleep 2
done
[ "${ready:-}" = 1 ] || { echo "pipeline not ready; check 'make ps' / 'make logs'" >&2; exit 1; }

baseline=$(check baseline)
echo "==> Before:"
curl -fsS localhost:8000/v1/beers/1 >/dev/null 2>&1 && echo "    (API on http://localhost:8000/docs)"
compose exec -T api python -c "
import json, urllib.request as u
g = lambda p: json.load(u.urlopen('http://127.0.0.1:8000' + p))
print('    beers:', g('/v1/beers?limit=1')['meta']['total'], ' consumption events:', g('/v1/consumption?limit=1')['meta']['total'])
print('    beer 1 abv:', g('/v1/beers/1')['data']['abv'], ' consumption 10 quantity_l:', g('/v1/consumption/10')['data']['quantity_l'])
"

echo "==> Applying scripts/simulate_changes.sql to Postgres"
compose exec -T postgres psql -q -U novisto -d brewery < scripts/simulate_changes.sql

echo "==> Polling the API until every change is visible"
check verify "$baseline"
