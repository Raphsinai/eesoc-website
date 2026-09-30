#!/usr/bin/env bash
# Put SMTP_PASS into the cluster's eesoc-secrets, without it leaking on the way.
#
#   ./scripts/set-smtp-secret.sh          # take the password from backend.env
#   ./scripts/set-smtp-secret.sh --ask    # type it instead (not echoed)
#
# Why not the obvious one-liner from the README:
#
#   kubectl create secret generic eesoc-secrets --from-literal=SMTP_PASS='...'
#
# Two problems. The password appears in argv, so it is visible to `ps` for as
# long as the command runs and is written to shell history forever. And `create`
# fails outright once the secret exists, so the usual next move is to delete and
# recreate it — which silently drops ICU_API_KEY and the S3 keys along with it.
#
# This patches the single key, leaving the others alone, and passes the value
# through a file rather than the command line.

set -euo pipefail

cd "$(dirname "$0")/.."

NAMESPACE="${NAMESPACE:-eesoc}"
SECRET="eesoc-secrets"
ASSUME_YES=0
SOURCE="backend.env"

for arg in "$@"; do
  case "$arg" in
    --ask) SOURCE="prompt" ;;
    --yes|-y) ASSUME_YES=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v kubectl >/dev/null || { echo "kubectl is not on PATH." >&2; exit 1; }

if [ "$SOURCE" = "prompt" ]; then
  read -rsp "SMTP password for eesocweb@ic.ac.uk: " PASSWORD; echo
else
  [ -f backend.env ] || { echo "No backend.env. Use --ask, or create it." >&2; exit 1; }
  # cut -d= -f2- keeps any '=' inside the password itself.
  PASSWORD="$(grep -m1 '^SMTP_PASS=' backend.env | cut -d= -f2- || true)"
fi

if [ -z "${PASSWORD:-}" ]; then
  echo "SMTP_PASS is empty. Fill it in backend.env, or run with --ask." >&2
  exit 1
fi

CONTEXT="$(kubectl config current-context)"
echo "context    $CONTEXT"
echo "namespace  $NAMESPACE"
echo "secret     $SECRET"
echo "password   ${#PASSWORD} characters"

if [ "$ASSUME_YES" -ne 1 ]; then
  # This writes to a live cluster, and `current-context` is easy to have
  # pointing somewhere else entirely.
  read -rp "Write SMTP_PASS to that cluster? [y/N] " reply
  [ "$reply" = "y" ] || [ "$reply" = "Y" ] || { echo "Nothing written."; exit 1; }
fi

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

# A file, not --from-literal: the value never reaches argv.
PATCH_FILE="$(mktemp)"
chmod 600 "$PATCH_FILE"
trap 'rm -f "$PATCH_FILE"' EXIT

# -w0 because GNU base64 wraps at 76 columns and a newline inside the value
# would be a password that silently does not match. BSD base64 (macOS) has no
# -w, hence the fallback through tr.
ENCODED="$(printf '%s' "$PASSWORD" | base64 | tr -d '\n')"
printf '{"data":{"SMTP_PASS":"%s"}}' "$ENCODED" > "$PATCH_FILE"

if kubectl -n "$NAMESPACE" get secret "$SECRET" >/dev/null 2>&1; then
  kubectl -n "$NAMESPACE" patch secret "$SECRET" --patch-file "$PATCH_FILE"
  echo "patched (other keys untouched)"
else
  # Not a here-string: `<<<` appends a newline, and a password with a trailing
  # \n authenticates nowhere while looking correct in every listing.
  PASS_FILE="$(mktemp)"
  chmod 600 "$PASS_FILE"
  trap 'rm -f "$PATCH_FILE" "$PASS_FILE"' EXIT
  printf '%s' "$PASSWORD" > "$PASS_FILE"
  kubectl -n "$NAMESPACE" create secret generic "$SECRET" \
    --from-file=SMTP_PASS="$PASS_FILE"
  echo "created — remember ICU_API_KEY and the S3 keys still need adding"
fi

# The api reads SMTP_PASS at startup, so the running pod keeps the old value.
if kubectl -n "$NAMESPACE" get deploy api >/dev/null 2>&1; then
  kubectl -n "$NAMESPACE" rollout restart deploy/api
  kubectl -n "$NAMESPACE" rollout status deploy/api --timeout=120s
else
  echo "No api deployment yet — it will pick this up when first deployed."
fi
