"""Every migration file has been applied to the database.

The api writes are meaningless against a database whose schema has not caught
up with the code, so the baseline suite checks that the applied-migrations
table knows about at least every `.sql` file we ship before any write test runs.
"""

from pathlib import Path

# The worktree root, reached from this file's location (tests/baseline/ -> up
# two levels). Counting from the real tree rather than a hard-coded number keeps
# the check honest: add a migration and the floor moves with it.
MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "backend" / "database" / "migrations"
)


def _shipped_migration_count() -> int:
    """How many migration files the repository actually contains."""
    return len(list(MIGRATIONS_DIR.glob("*.sql")))


def test_migrations_table_has_every_shipped_file(compose_exec):
    """The migrations table records at least one row per shipped `.sql` file.

    `>=` rather than `==` on purpose: the table may also backfill pre-runner
    migrations, so it can legitimately hold more rows than there are files.
    Fewer rows than files is the failure that matters.
    """
    stdout = compose_exec(
        "postgres",
        "psql",
        "-U",
        "eesoc",
        "-d",
        "eesoc",
        "-tAc",
        "SELECT count(*) FROM migrations",
    )
    applied = int(stdout.strip())
    shipped = _shipped_migration_count()
    assert shipped > 0, "no migration files found under backend/database/migrations"
    assert applied >= shipped, (
        f"database has {applied} applied migrations but the repo ships {shipped}"
    )