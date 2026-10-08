"""Contract tests for scripts/check-migrations.py.

Zero-downtime rolls depend on migrations only ever adding: new tables, new
columns (nullable or with a DEFAULT), new indexes. A drop, rename, or NOT NULL
column without a DEFAULT breaks a service that did not stop writing, so CI
needs a gate that fails the build the moment a migration violates that. These
tests pin the gate's CLI contract rather than its internals:

  * exit 0 for a clean `.sql` file or directory,
  * exit 1 with one `file:line: <reason>` per violation,
  * the `-- contract-step: <reason>` marker lets a reviewed break through and
    is printed on stdout.

Pure subprocess tests: they run the script with `sys.executable` from the repo
root and never touch compose, so they run in every pytest environment.
"""

import subprocess
import sys
from pathlib import Path

import pytest

# The worktree root, reached from this file's location (tests/zero-downtime/ ->
# up two levels). Running the script with cwd set here mirrors how CI invokes
# it, so relative paths inside the script resolve the same way.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-migrations.py"
FIXTURES = Path(__file__).parent / "fixtures"
MIGRATIONS_DIR = REPO_ROOT / "backend" / "database" / "migrations"

# The script is written by the section's implementer in parallel with these
# tests. Until it lands, fail cleanly by skipping the whole module rather than
# erroring on a missing file — this marker is the one sanctioned fallback.
pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(),
    reason="scripts/check-migrations.py not implemented yet",
)


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke the check exactly as CI would: from the repo root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _output(result: subprocess.CompletedProcess) -> str:
    """stdout and stderr joined, since the script may print a violation to either."""
    return f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    "fixture, keyword",
    [
        ("bad_drop_table.sql", "DROP TABLE"),
        ("bad_drop_column.sql", "DROP COLUMN"),
        ("bad_rename.sql", "RENAME"),
        ("bad_not_null.sql", "NOT NULL"),
    ],
)
def test_contract_violation_fails(fixture, keyword):
    """Each destructive pattern exits 1 and names the file and the rule.

    The exact wording of the reason is the implementer's, so this keys on the
    violation keyword plus the file being present in the `file:line: <reason>`
    output — enough to prove the gate caught the specific break rather than
    failing the file for some unrelated reason.
    """
    result = _run(str(FIXTURES / fixture))
    assert result.returncode == 1, (
        f"expected exit 1 for {fixture}, got {result.returncode}\n{_output(result)}"
    )
    output = _output(result)
    assert fixture in output, f"output does not name {fixture}:\n{output}"
    assert keyword in output, f"output does not mention {keyword!r}:\n{output}"


def test_contract_step_marker_allows_destructive_change():
    """A violating file with `-- contract-step: <reason>` passes and prints why.

    The marker is the reviewed-approval escape hatch: exit 0 so the build
    proceeds, and the reason echoed on stdout so a reviewer sees what was
    deliberately accepted.
    """
    result = _run(str(FIXTURES / "marked_contract_step.sql"))
    assert result.returncode == 0, _output(result)
    assert "contract-step" in result.stdout, result.stdout
    assert "merging duplicates" in result.stdout, result.stdout


def test_additive_migration_passes():
    """New tables, DEFAULTed columns and indexes all exit clean."""
    result = _run(str(FIXTURES / "ok_additive.sql"))
    assert result.returncode == 0, _output(result)


def test_real_migrations_pass():
    """Every migration the gate applies to is expand-only.

    The directory mixes two kinds of file: 000-034, which shipped before the
    rule existed and are grandfathered (they contain renames and drops, and
    they are immutable now), and 035 onward, which the rule gates. The
    assertion is "exit 0 on whatever is there", so the gate stays honest as
    migrations accrete rather than freezing at today's count.
    """
    result = _run(str(MIGRATIONS_DIR))
    assert result.returncode == 0, _output(result)