#!/usr/bin/env bash
# Load the latest scrubbed production snapshot into STAGING.
#
# Runs in the staging namespace as an ArgoCD PostSync hook, so it happens after
# every staging deploy. It replaces staging's database wholesale: anything
# edited in the staging admin dashboard since the last refresh is discarded,
# which is the point — staging is a disposable copy, and edits there must never
# be mistaken for a change to the real site.
#
# Everything this script can reach is read-only except staging's own database.
# The snapshot comes from a bucket key with read permission and nothing else,
# so there is no sequence of mistakes here that writes to production.
set -euo pipefail

say() { echo "[restore] $*"; }

# ---------------------------------------------------------------------------
# The guard. Refuse to run anywhere but staging.
# ---------------------------------------------------------------------------
# A restore is a DROP SCHEMA. If the connection details were ever wrong — a
# copied manifest, a secret pointed at the wrong namespace, an overlay applied
# to the wrong cluster — this script would destroy the live database and the
# first anyone would know is the site going blank. So it checks what it is
# about to overwrite, and stops unless everything says staging.
if [ "${ENVIRONMENT:-}" != "staging" ]; then
    echo "[restore] REFUSING: ENVIRONMENT is '${ENVIRONMENT:-unset}', not 'staging'" >&2
    exit 1
fi

# The namespace comes from the Downward API, so it is what Kubernetes says this
# pod actually is — not a string somebody typed into a manifest that could have
# been copied from production.
EXPECT_NS=${EXPECT_NAMESPACE:-eesoc-staging}
if [ "${POD_NAMESPACE:-}" != "$EXPECT_NS" ]; then
    echo "[restore] REFUSING: running in namespace '${POD_NAMESPACE:-unset}', expected '$EXPECT_NS'" >&2
    exit 1
fi

# And the database this is about to drop must be one inside that namespace.
#
# A bare service name (no dots) can only resolve within the pod's own
# namespace, so it is safe by construction once the check above has passed.
# A qualified name can point anywhere, so it has to name this namespace
# explicitly — `eesoc-db-rw.eesoc.svc` from a staging pod is exactly the
# accident this catches, and the cost of missing it is the live database.
case "${PGHOST:-}" in
    "")   echo "[restore] REFUSING: PGHOST is unset" >&2; exit 1 ;;
    *.*)  case "$PGHOST" in
              *".$EXPECT_NS."*|*".$EXPECT_NS") ;;
              *) echo "[restore] REFUSING: PGHOST '$PGHOST' is outside namespace '$EXPECT_NS'" >&2
                 exit 1 ;;
          esac ;;
    *)    ;;  # bare name — resolves inside $EXPECT_NS
esac

say "target checks passed: ${PGUSER}@${PGHOST}/${PGDATABASE}"

SNAPSHOT=${SNAPSHOT:-/scratch/snapshot.sql.gz}
[ -s "$SNAPSHOT" ] || { echo "[restore] no snapshot at $SNAPSHOT" >&2; exit 1; }
say "restoring $(wc -c < "$SNAPSHOT") bytes"

# ---------------------------------------------------------------------------
# Replace the schema in ONE transaction.
# ---------------------------------------------------------------------------
# DROP and reload together, so a failure half way leaves staging on the
# previous snapshot rather than on no schema at all. Postgres makes DDL
# transactional, which is what lets a restore be all-or-nothing.
{
    echo "BEGIN;"
    echo "DROP SCHEMA public CASCADE;"
    echo "CREATE SCHEMA public;"
    gunzip -c "$SNAPSHOT"
    echo "COMMIT;"
} | psql -v ON_ERROR_STOP=1 -q

say "restored"

# The api and the adapter read nothing at startup that this changes, but the
# session table is now empty: anyone signed in to staging is signed out. That is
# correct — those sessions belonged to a database that no longer exists.
psql -tAc "SELECT 'rows in users: ' || count(*) FROM public.users"
say "done"
