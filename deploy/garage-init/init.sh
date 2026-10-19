#!/bin/sh
# Bring a fresh Garage up to a usable state.
#
# Runs as a sidecar in the Garage pod rather than as a Job, for two reasons:
# the CLI authenticates using a node key held in the metadata directory, which
# only a container in the same pod can read without fighting over a
# ReadWriteOnce volume; and the node is already at its pod limit, so a sidecar
# costs nothing where another pod would not schedule at all.
#
# Re-run safe, because the container restarts with the pod: every step checks
# before it acts.
set -eu

BUCKET="${GARAGE_BUCKET:-eesoc-media}"
KEY_NAME="${GARAGE_KEY_NAME:-eesoc-api}"
ZONE="${GARAGE_ZONE:-dc1}"
CAPACITY="${GARAGE_CAPACITY:-20G}"

# The node answers RPC before it has finished starting, so poll rather than
# assume. A failed hook blocks the whole sync.
i=0
until garage status >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && echo "garage did not become reachable" && exit 1
  sleep 2
done

# A node with no role holds no data. Assigning one is what makes the cluster
# usable, and it must happen exactly once — re-assigning would stage a layout
# change on every sync and churn the cluster.
NODE="$(garage status | awk '/NO ROLE ASSIGNED/{print $1}')"
if [ -n "$NODE" ]; then
  echo "assigning layout to $NODE"
  garage layout assign -z "$ZONE" -c "$CAPACITY" "$NODE"
  VERSION="$(garage layout show | awk -F': ' '/Current cluster layout version/{print $2}')"
  garage layout apply --version "$((VERSION + 1))"
else
  echo "layout already assigned"
fi

if garage bucket list | grep -q "[[:space:]]${BUCKET}[[:space:]]*"; then
  echo "bucket ${BUCKET} exists"
else
  garage bucket create "$BUCKET"
fi

# The key is imported rather than generated: the credentials are already in
# eesoc-secrets, which the api reads. Letting Garage mint them instead would
# mean copying a generated secret back out of a pod and into Kubernetes by
# hand on every rebuild.
if garage key list | grep -q "$S3_ACCESS_KEY_ID"; then
  echo "key already imported"
else
  # Output suppressed: `key import` echoes the secret key back, and this runs
  # in a container whose logs are readable by anyone with pod read access.
  garage key import --yes -n "$KEY_NAME" \
    "$S3_ACCESS_KEY_ID" "$S3_SECRET_ACCESS_KEY" >/dev/null 2>&1
  echo "imported key $KEY_NAME"
fi

# Both are idempotent in Garage itself.
garage bucket allow --read --write "$BUCKET" --key "$KEY_NAME"
# Anonymous reads. Garage has no public-read ACL on the S3 API, so unauthenticated
# GETs are served by the web endpoint instead; this is what opens it.
garage bucket website --allow "$BUCKET"

# ---------------------------------------------------------------------------
# Snapshot bucket and the read-only key staging uses. Production only: the
# variables below are set in the production overlay and absent in staging, so
# staging's own Garage never grows a snapshot bucket it has nothing to put in.
# ---------------------------------------------------------------------------
if [ -n "${SNAPSHOT_BUCKET:-}" ]; then
  if garage bucket list | grep -q "[[:space:]]${SNAPSHOT_BUCKET}[[:space:]]*"; then
    echo "bucket ${SNAPSHOT_BUCKET} exists"
  else
    garage bucket create "$SNAPSHOT_BUCKET"
  fi

  # Deliberately NO `bucket website --allow` here. The media bucket is served
  # anonymously because event posters are meant to be public; this one holds a
  # database dump. Scrubbed, but a dump of the site's whole structure and
  # content is not something to leave on an unauthenticated endpoint.

  # The snapshot job writes here with the api's existing key.
  garage bucket allow --read --write "$SNAPSHOT_BUCKET" --key "$KEY_NAME"

  # The key staging authenticates with. READ ONLY, on purpose and in two
  # places: Garage enforces it, and staging's only route here,
  # snapshot.eesoc.com, passes reads of one object and nothing else. Staging
  # therefore cannot write to production's buckets even if its own code is
  # wrong, which is what keeps a refresh from ever running backwards.
  if [ -n "${STAGING_RO_ACCESS_KEY_ID:-}" ]; then
    if garage key list | grep -q "$STAGING_RO_ACCESS_KEY_ID"; then
      echo "staging read-only key already imported"
    else
      garage key import --yes -n eesoc-staging-ro \
        "$STAGING_RO_ACCESS_KEY_ID" "$STAGING_RO_SECRET_ACCESS_KEY" >/dev/null 2>&1
      echo "imported staging read-only key"
    fi
    garage bucket allow --read "$SNAPSHOT_BUCKET" --key eesoc-staging-ro
    garage bucket allow --read "$BUCKET"          --key eesoc-staging-ro
    # Belt and braces: if this key ever had write granted by hand, take it back.
    garage bucket deny --write "$SNAPSHOT_BUCKET" --key eesoc-staging-ro 2>/dev/null || true
    garage bucket deny --write "$BUCKET"          --key eesoc-staging-ro 2>/dev/null || true
  fi
fi

echo "garage ready: bucket=${BUCKET} key=${KEY_NAME}"
