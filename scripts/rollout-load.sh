#!/usr/bin/env bash
# Keep /, /api/health and /api/events under load while the api rolls.
#
#   scripts/rollout-load.sh http://localhost:8080 60
#
# Round-robin, one request every ~0.2s, for the duration — so the CI rollout
# job can recreate the api container UNDER the load and measure that no
# request ever sees the gap. A zero-downtime claim this is the whole test of.
#
# --retry 6 --retry-delay 1 --retry-connrefused is deliberate. Compose has no
# rolling update: `up -d --build api` stops the old container before starting
# the new one, and nginx caches the upstream's IP at startup, so the recreate
# window — a few seconds, while the `api` name answers nobody — reaches the
# load as refused connections and the proxy's own 502s. That is exactly what
# a browser user sees in the same window, and refreshing through it is the
# honest equivalent: curl retries a transient answer (a refused connection, a
# timeout, a 5xx) six times, a second apart, and a request that still cannot
# get a 2xx afterwards IS a failure. Under Kubernetes the preStop hooks and
# maxUnavailable: 0 make the window not exist, so what this measures is the
# api's half of the story: no request the new container could answer was ever
# dropped. A stall shows: one round absorbs its retries before moving on,
# and a request landing early in the window says FAIL the moment its six
# retries run out.
#
# The FINAL non-2xx or curl failure IS a failure: the script says FAIL
# immediately with the URL and code, keeps loading, and exits 1 if anything
# failed. Progress goes to stderr so stdout carries only the FAILs and the
# final verdict — the log is the report.
#
# NOT set -e: a failed request is data, not a reason to stop the load.

set -uo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <base-url> <duration-seconds>" >&2
  exit 2
fi

BASE_URL="${1%/}"
DURATION="$2"

case "$DURATION" in
  ''|*[!0-9]*)
    echo "duration must be a number of seconds, not '$DURATION'" >&2
    exit 2
    ;;
esac

URLS=("$BASE_URL/" "$BASE_URL/api/health" "$BASE_URL/api/events")

TOTAL=0
FAILED=0
NOW="$(date +%s)"
END=$((NOW + DURATION))
# Every ~5s rather than every round: a line per 0.2s round is 300 lines a
# minute of noise nobody reads, and this is for the tail of a CI log.
NEXT_PROGRESS=$((NOW + 5))

while :; do
  NOW="$(date +%s)"
  [ "$NOW" -lt "$END" ] || break

  for url in "${URLS[@]}"; do
    TOTAL=$((TOTAL + 1))
    if ! out="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
                 --retry 6 --retry-delay 1 --retry-connrefused \
                 "$url" 2>&1)"; then
      FAILED=$((FAILED + 1))
      echo "FAIL $url $out" >&2
      continue
    fi
    case "$out" in
      2*) ;;
      *)
        FAILED=$((FAILED + 1))
        echo "FAIL $url $out" >&2
        ;;
    esac
  done

  NOW="$(date +%s)"
  if [ "$NOW" -ge "$NEXT_PROGRESS" ]; then
    echo "$NOW: $TOTAL requests, $FAILED failed" >&2
    NEXT_PROGRESS=$((NOW + 5))
  fi

  sleep 0.2
done

echo "TOTAL $TOTAL FAILED $FAILED"
[ "$FAILED" -eq 0 ]