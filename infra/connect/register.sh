#!/bin/sh
# Idempotent: PUT /connectors/<name>/config creates the connector or updates it in place.
set -eu

CONNECT_URL="${CONNECT_URL:-http://connect:8083}"
NAME="${CONNECTOR_NAME:-postgres-cdc}"
CONFIG="${CONNECTOR_CONFIG:-/config/postgres-cdc.json}"

until curl -fsS "$CONNECT_URL/connectors" >/dev/null; do
  echo "waiting for kafka connect at $CONNECT_URL ..."
  sleep 2
done

curl -fsS -X PUT -H 'Content-Type: application/json' \
  --data @"$CONFIG" "$CONNECT_URL/connectors/$NAME/config" >/dev/null

for _ in $(seq 1 30); do
  state=$(curl -fsS "$CONNECT_URL/connectors/$NAME/status" 2>/dev/null | sed -n 's/.*"tasks":\[{"id":0,"state":"\([A-Z]*\)".*/\1/p')
  if [ "$state" = "RUNNING" ]; then
    echo "connector $NAME is RUNNING"
    exit 0
  fi
  sleep 2
done

echo "connector $NAME did not reach RUNNING:" >&2
curl -sS "$CONNECT_URL/connectors/$NAME/status" >&2
exit 1
