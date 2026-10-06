#!/usr/bin/env bash
# Produce a scrubbed snapshot of production, for staging to restore.
#
# Runs in the PRODUCTION namespace, on a schedule. It is the only half of this
# pipeline that can read real data, and it never talks to staging — it writes an
# object to a bucket and stops. Staging reads that object with a key that can do
# nothing else. There is no code path from staging back to production, which is
# what makes "refresh staging from prod" unable to become "overwrite prod from
# staging" after a bad edit.
#
# The scrub happens in a throwaway Postgres inside this pod, not in the live
# database and not on the staging side: raw personal data is never written
# anywhere outside this container, and the snapshot that leaves is verified
# clean before it is uploaded.
set -euo pipefail

SCRATCH=${SCRATCH:-/scratch}
export PGDATA="$SCRATCH/pg"
SOCK="$SCRATCH"

say() { echo "[snapshot] $*"; }

# Whatever happens — a failed verification, a dropped connection, a SIGTERM —
# the throwaway cluster holding the unscrubbed copy is destroyed on the way
# out. Without this, a job that fails verification leaves a full unredacted
# database sitting in the pod's volume until somebody deletes the pod, which is
# exactly the state this whole script exists to avoid.
cleanup() {
    local rc=$?
    if [ -d "$PGDATA" ]; then
        pg_ctl -D "$PGDATA" -m immediate -w stop >/dev/null 2>&1 || true
        rm -rf "$PGDATA"
        say "cleaned up throwaway postgres"
    fi
    rm -f "$SCRATCH/raw.sql"
    [ "$rc" -ne 0 ] && say "FAILED (exit $rc) — nothing was published"
    return $rc
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Dump production. Read-only; --no-owner/--no-privileges because the
#    throwaway cluster and staging both use different role names.
# ---------------------------------------------------------------------------
say "dumping production database ${PGDATABASE} from ${PGHOST}"
pg_dump --no-owner --no-privileges --format=plain --file="$SCRATCH/raw.sql"
say "dumped $(wc -c < "$SCRATCH/raw.sql") bytes"

# ---------------------------------------------------------------------------
# 2. Restore it into a private Postgres that exists only inside this pod.
#    listen_addresses='' means it has no TCP socket at all — nothing outside
#    this container can reach the unscrubbed copy, not even in-cluster.
# ---------------------------------------------------------------------------
say "starting throwaway postgres"
initdb -D "$PGDATA" -U postgres --auth=trust >/dev/null
pg_ctl -D "$PGDATA" -o "-c listen_addresses='' -k $SOCK" -w start >/dev/null

scratch_psql() { psql -h "$SOCK" -U postgres -d snapshot -v ON_ERROR_STOP=1 "$@"; }

psql -h "$SOCK" -U postgres -d postgres -v ON_ERROR_STOP=1 \
     -c "CREATE DATABASE snapshot" >/dev/null
scratch_psql -q -f "$SCRATCH/raw.sql" >/dev/null

# The plain-text dump is the most exposed form the data takes. Delete it the
# moment it is loaded rather than at the end of the script.
shred -u "$SCRATCH/raw.sql" 2>/dev/null || rm -f "$SCRATCH/raw.sql"
say "restored into throwaway, raw dump deleted"

# ---------------------------------------------------------------------------
# 3. Scrub, then PROVE it. verify.sql raises, which with ON_ERROR_STOP aborts
#    the job before anything is uploaded — a snapshot that cannot be shown to
#    be clean is never published.
# ---------------------------------------------------------------------------
say "scrubbing"
scratch_psql -q -f /snapshot/scrub.sql

say "verifying"
scratch_psql -f /snapshot/verify.sql

# ---------------------------------------------------------------------------
# 4. Re-dump the scrubbed copy. This is what staging will restore.
# ---------------------------------------------------------------------------
pg_dump -h "$SOCK" -U postgres -d snapshot --no-owner --no-privileges \
  | gzip -9 > "$SCRATCH/snapshot.sql.gz"
say "scrubbed snapshot is $(wc -c < "$SCRATCH/snapshot.sql.gz") bytes"

# The upload container takes it from here; it waits on this marker so it can
# never upload a half-written or unverified file.
touch "$SCRATCH/.ready"
say "done"
