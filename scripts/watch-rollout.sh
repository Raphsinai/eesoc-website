#!/usr/bin/env bash
# Watch a staging deploy roll through — loudly, immediately, by name.
#
#   scripts/watch-rollout.sh                          # staging
#   scripts/watch-rollout.sh https://eesoc.com
#
# This is for Raph to run in a second terminal DURING a real staging deploy:
# paste it in before pushing, and watch the rollout happen request by
# request. Its whole job is to stop at the FIRST failed request and name
# the URL and the code — one 500 mid-rollout is the thing a screenshot
# afterwards cannot show, so dying instantly with "FAIL <url> <code>" beats
# a summary that quietly mentions a blip three pages of ok ago.
#
# Four URLs per second: /, /api/health, /api/events, and one hashed asset
# (/assets/<name>.js or .css) picked off the index page once up front. The
# asset name changes with every build, so it is the one request that proves
# the browser is being served the NEW bundle, not the old one cached. All
# four are a hard requirement, not a best effort: this script exits
# non-zero if it cannot watch them all, because a three-URL watch would
# quietly green-light the one request the whole check exists for.
#
# NOT set -e: the loop handles failures itself, and a trap needs its
# variables for the summary rather than a script that dies mid-curl.

set -uo pipefail

BASE_URL="${1:-https://eesoc.raphsinai.dev}"
BASE_URL="${BASE_URL%/}"

POLLS=0

# Ctrl-C means "I have seen enough", not "it broke": say how far it got.
summary() {
  echo
  echo "$POLLS poll(s) completed, 0 failures — stopping."
  exit 130
}
trap summary INT

# The hashed asset name, discovered before watching starts. Discovery is
# bounded but not skippable. A watch pasted in just as the deploy recreates a
# pod — the exact moment this script exists for — can catch the index
# genuinely down, so a curl that cannot connect retries, about 30 attempts
# ~1s apart, rather than failing a watch that a second run would have to
# redo. An index that ANSWERS but carries no /assets/<name>.(js|css) is the
# other case: that is a broken deploy, not a busy one, and watching three
# URLs while silently dropping the one request that proves the new bundle
# is what a skipped check would mean — so it exits non-zero, as does
# never connecting within the 30 attempts.
ASSET_PATH=""
ATTEMPT=0
while [ -z "$ASSET_PATH" ] && [ "$ATTEMPT" -lt 30 ]; do
  ATTEMPT=$((ATTEMPT + 1))
  # Body and stderr together: on success that is the index page (any curl
  # warning lands in it too, which the grep below does not match); on a
  # failed connect it is curl's own error, echoed by the retry line so a
  # stuck discovery says why instead of hanging silently. Max-time 2, not
  # the watch loop's 5: discovery retries about once a second, and a
  # connect that hangs would otherwise eat ten of those seconds a time.
  if ! body="$(curl -sS --max-time 2 "$BASE_URL/" 2>&1)"; then
    echo "$BASE_URL/ not answering yet (attempt $ATTEMPT/30): $body"
    sleep 1
    continue
  fi
  ASSET_PATH="$(grep -oE '/assets/[A-Za-z0-9._-]+\.(js|css)' <<<"$body" | head -n 1 || true)"
  if [ -z "$ASSET_PATH" ]; then
    echo "FAIL $BASE_URL/ answered with no /assets/<name>.(js|css) — broken index, not a busy one"
    exit 1
  fi
done
if [ -z "$ASSET_PATH" ]; then
  echo "FAIL $BASE_URL/ never answered in 30 attempts (~30s) — nothing to watch"
  exit 1
fi

URLS=("$BASE_URL/" "$BASE_URL/api/health" "$BASE_URL/api/events" "$BASE_URL$ASSET_PATH")
LABELS=("/" "/api/health" "/api/events" "$ASSET_PATH")

echo "watching $BASE_URL every ~1s (Ctrl-C to stop)"
echo

while :; do
  POLLS=$((POLLS + 1))
  line="$(date -u '+%Y-%m-%d %H:%M:%S')"
  i=0
  while [ "$i" -lt "${#URLS[@]}" ]; do
    url="${URLS[$i]}"
    label="${LABELS[$i]}"
    # -w '%{http_code}' prints the code; on failure curl's own error is
    # merged in from stderr, so the FAIL line says why, not just "000".
    if ! out="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>&1)"; then
      echo "FAIL $url $out"
      exit 1
    fi
    case "$out" in
      2*) ;;
      *)
        echo "FAIL $url $out"
        exit 1
        ;;
    esac
    line="$line  $out ${label}"
    i=$((i + 1))
  done
  echo "$line"
  sleep 1
done