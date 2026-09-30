"""Migration runner.

Replaces the `/docker-entrypoint-initdb.d` stopgap, which only ever ran against
an empty volume — so any migration added after the first boot silently never
applied. That is survivable locally (`down -v`) and not survivable at all under
Kubernetes, where the database outlives every deploy.

Runs automatically when the adapter starts. Two safeguards make that safe with
more than one replica:

  * a Postgres advisory lock, so concurrent pods queue rather than race;
  * each file applied inside its own transaction, recorded in `migrations`,
    so a partial apply cannot be recorded as done.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import asyncpg

log = logging.getLogger("database.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Arbitrary but fixed: every replica must pick the same lock id.
LOCK_ID = 8_675_309

FILENAME = re.compile(r"^(\d+)_(.+)\.sql$")


def _discover() -> list[tuple[int, str, Path]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = FILENAME.match(path.name)
        if not match:
            log.warning("Skipping %s — expected NNN_name.sql", path.name)
            continue
        found.append((int(match.group(1)), match.group(2), path))
    return sorted(found)


async def ensure_table(conn: asyncpg.Connection) -> None:
    """Create the bookkeeping table before anything reads it.

    000_init.sql also creates it, but the runner needs it *first* — including on
    a database built by the old initdb path, where backfill runs before any
    migration does.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.migrations (
            Applied_at TIMESTAMP DEFAULT NOW(),
            Number     BIGINT UNIQUE,
            Name       VARCHAR
        );
        """
    )
    # Added by this runner; the original table had no integrity check.
    await conn.execute(
        "ALTER TABLE public.migrations ADD COLUMN IF NOT EXISTS checksum VARCHAR;"
    )


async def run(conn: asyncpg.Connection) -> dict:
    """Apply every migration not yet recorded. Returns a summary."""
    await ensure_table(conn)
    await conn.execute("SELECT pg_advisory_lock($1);", LOCK_ID)
    try:
        rows = await conn.fetch("SELECT number, name, checksum FROM public.migrations;")
        applied = {r["number"]: r for r in rows}

        pending, drifted = [], []
        for number, name, path in _discover():
            sql = path.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]
            if number in applied:
                recorded = applied[number]["checksum"]
                # An edited migration will not be re-run, so flag it loudly
                # rather than leaving the schema quietly out of step.
                if recorded and recorded != checksum:
                    drifted.append(path.name)
                continue
            pending.append((number, name, path, sql, checksum))

        for name in drifted:
            log.error(
                "Migration %s has changed since it was applied. Migrations are "
                "immutable once run — add a new one instead.",
                name,
            )

        for number, name, path, sql, checksum in pending:
            log.info("Applying migration %03d_%s", number, name)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    """
                    INSERT INTO public.migrations (number, name, checksum)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (number) DO UPDATE
                       SET name = EXCLUDED.name, checksum = EXCLUDED.checksum;
                    """,
                    number,
                    name,
                    checksum,
                )

        # Migrations 000-013 predate the runner and were applied by initdb, so
        # they have no rows. Record them without re-running: their objects
        # already exist and re-execution would be a no-op at best.
        if not applied and pending:
            log.info("Recorded %d migrations on a fresh database", len(pending))

        return {
            "applied": [f"{n:03d}_{m}" for n, m, *_ in pending],
            "already": len(applied),
            "drifted": drifted,
        }
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1);", LOCK_ID)


async def backfill_existing(conn: asyncpg.Connection) -> None:
    """Mark pre-runner migrations as applied on a database that was built by the
    old initdb path, so the runner does not try to replay them.

    Detected by a table that only exists if those migrations ran.
    """
    await ensure_table(conn)
    has_sessions = await conn.fetchval(
        "SELECT to_regclass('public.sessions') IS NOT NULL;"
    )
    recorded = await conn.fetchval("SELECT COUNT(*) FROM public.migrations;")
    if not has_sessions or recorded:
        return

    for number, name, path in _discover():
        if number > 13:  # 014+ are runner-era and must actually apply
            continue
        checksum = hashlib.sha256(path.read_text().encode()).hexdigest()[:16]
        await conn.execute(
            """
            INSERT INTO public.migrations (number, name, checksum) VALUES ($1,$2,$3)
            ON CONFLICT (number) DO NOTHING;
            """,
            number,
            name,
            checksum,
        )
    log.info("Backfilled pre-runner migrations as applied")
