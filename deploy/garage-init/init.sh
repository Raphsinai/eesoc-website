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

echo "garage ready: bucket=${BUCKET} key=${KEY_NAME}"
