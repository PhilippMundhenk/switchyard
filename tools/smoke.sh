#!/usr/bin/env bash
# Container smoke test: build the image, run it in demo mode, drive the API.
set -euo pipefail
IMAGE=${IMAGE:-switchyard:smoke}
NAME=switchyard-smoke
PORT=${PORT:-18080}
PASS=smoke-password-1
JAR=$(mktemp)
cleanup() {
  status=$?
  if [ $status -ne 0 ]; then docker logs "$NAME" 2>&1 | tail -50 || true; fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -f "$JAR"
  exit $status
}
trap cleanup EXIT

[ -z "${SKIP_BUILD:-}" ] && docker build -t "$IMAGE" .
docker run -d --name "$NAME" -p "$PORT:8080" \
  -e SWITCHYARD_DEMO_ENABLED=true -e SWITCHYARD_AUTH_PASSWORD="$PASS" \
  -e SWITCHYARD_NSDP_POLL_SECONDS=0 "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME")" = healthy ] && break
  sleep 1
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME")" = healthy ] || { echo "not healthy"; exit 1; }

B=http://127.0.0.1:$PORT
curl -fsS "$B/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok" and d["demo"] is True'
curl -fsS -c "$JAR" -H 'Content-Type: application/json' -d "{\"password\":\"$PASS\"}" "$B/api/login" >/dev/null
curl -fsS -b "$JAR" -X POST "$B/api/discovery/scan" | python3 -c 'import json,sys; f=json.load(sys.stdin)["found"]; assert len(f)==6 and {x["protocol"] for x in f}=={"nsdp","easysmart"}'
MAC=00:26:f2:aa:10:01
curl -fsS -b "$JAR" -H 'Content-Type: application/json' -d "{\"mac\":\"$MAC\",\"password\":\"password\"}" "$B/api/devices" >/dev/null
curl -fsS -b "$JAR" "$B/api/devices/$MAC/state" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["info"]["model"]=="GS108E" and len(d["ports"])==8'
TP=50:c7:bf:5e:00:01  # TP-Link Easy Smart simulator
curl -fsS -b "$JAR" -H 'Content-Type: application/json' -d "{\"mac\":\"$TP\",\"username\":\"admin\",\"password\":\"admin\"}" "$B/api/devices" >/dev/null
curl -fsS -b "$JAR" "$B/api/devices/$TP/state" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["info"]["model"]=="TL-SG108PE" and len(d["ports"])==8'
test "$(curl -s -o /dev/null -w '%{http_code}' "$B/api/devices")" = 401
curl -fsS "$B/" | grep -q Switchyard
curl -fsS "$B/favicon.svg" | grep -q "<svg"
[ "$(docker exec "$NAME" id -u)" != 0 ] || { echo "runs as root"; exit 1; }
echo "smoke test passed"
