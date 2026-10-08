#!/usr/bin/env python3
"""Gate migrations to expand-only, unless a reviewer marked a contract step.

    python3 scripts/check-migrations.py backend/database/migrations
    python3 scripts/check-migrations.py path/to/one.sql [path ...]

A path may be a `.sql` file or a directory, checked non-recursively for
`*.sql` (that is exactly the shape of `backend/database/migrations`, and
recursive would pick up the fixtures under tests/).

Zero-downtime rolls depend on old and new pods running side by side, which
only works if a migration never removes or narrows anything the *old* code
still reads. So a migration file may only expand:

  * a new table, a new index, or a new column that is nullable or has a
    DEFAULT — all apply without breaking a replica that is mid-deploy.

A file that contracts instead fails the gate:

  * DROP TABLE / DROP COLUMN — the old pod still reads that shape;
  * RENAME inside ALTER — a break in place: every reader and writer of the
    old name stops the moment it applies, so a rename needs the same
    expand (new name) → deploy → contract (drop old) cycle as a drop;
  * NOT NULL on an ADD COLUMN without a DEFAULT — not an expand at all:
    it fails outright on any table that already has rows, so it cannot be
    applied as one step while the old code keeps running.

The escape hatch is a marker: `-- contract-step: <reason>` anywhere in the
file says a reviewer decided this contract break lands now, and checked that
nothing running depends on the old shape. The file passes and the marker's
reason is printed, so the acceptance is visible in the CI log rather than
buried in a diff.

The rule is prospective, and that is not a weakness: migrations 000–034
shipped before it existed, and an applied migration is immutable now —
re-editing one is how a database and its git history drift apart — so the
gate starts at 035, the first migration written under the rule. Files
without a leading number (the test fixtures) are gated regardless, because
they exist to be judged.

SQL comments are stripped before scanning (-- line comments and /* block */
comments), so a migration whose *prose* says "rename" or "drop" is not
flagged for its commentary — the marker, though, is a comment, so it is
looked for in the raw text before stripping.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Sought in the raw text, not the stripped SQL: the marker IS a comment, and
# stripping it would erase the reviewer's voice along with the prose.
MARKER = re.compile(r"--\s*contract-step:\s*(\S.*?)\s*$")

# The first migration number the gate applies to. See the module docstring:
# 000–034 shipped before the rule existed and are grandfathered, because an
# applied migration cannot be edited now — the only lever the rule has over
# already-shipped files would be breaking their immutability, which is the
# worse contract. Everything from 035 on was written under the rule.
GATE_FROM = 35

# Violations, sought in the stripped SQL only.
DROP_TABLE = re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)
DROP_COLUMN = re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE)
# RENAME never appears in Postgres DDL outside an ALTER, so a bare word match
# is enough without tracking which ALTER statement a line belongs to.
RENAME = re.compile(r"\bRENAME\b", re.IGNORECASE)
ADD_COLUMN = re.compile(r"\bADD\s+COLUMN\b(?P<clause>[^\n;]*)", re.IGNORECASE)
NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
HAS_DEFAULT = re.compile(r"\bDEFAULT\b", re.IGNORECASE)

REASONS = [
    (DROP_TABLE,
     "DROP TABLE is a contract step, not an expand step — drop it only in a "
     "LATER deploy than the code that reads it, behind a "
     "`-- contract-step:` marker"),
    (DROP_COLUMN,
     "DROP COLUMN is a contract step, not an expand step — stop writing it, "
     "deploy, then drop it in a LATER deploy behind a "
     "`-- contract-step:` marker"),
    (RENAME,
     "RENAME is a break in place, not an expand step — readers of the old "
     "name stop the moment it applies; expand (add the new name), deploy, "
     "then drop the old in a LATER deploy behind a `-- contract-step:` "
     "marker"),
]


def _strip_comments(sql: str) -> str:
    """Blank out SQL comments so keyword scans see only real statements.

    `/* ... */` may span lines; the newlines inside are replaced too, so a
    keyword never leaks from a comment into what the scanner sees. Line
    comments stop at the newline by construction, and string literals are
    (deliberately) not treated specially: no shipped migration carries SQL
    keywords in data, and a checker that misfires on prose is one people
    work around rather than trust.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def check_file(path: Path) -> int:
    """Check one file. Returns its violation count; markers printed as found.

    `path` is echoed in every line exactly as it was given, so CI output can
    be clicked through from the violation to the file regardless of how the
    checker was invoked.
    """
    # Grandfathering: a numbered file below GATE_FROM shipped before the rule,
    # so it is exempt. Unnumbered files (the fixtures) carry no number and are
    # gated — a quiet skip rather than a printed note, so a directory check of
    # 35 grandfathered files does not bury the violations it also found.
    number = re.match(r"(\d+)", path.name)
    if number and int(number.group(1)) < GATE_FROM:
        return 0

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{path}: could not read ({exc})", file=sys.stderr)
        return 1

    # The marker comes first and short-circuits: a marked file's violations
    # are the point of the marker, not news to report.
    markers = [m for line in text.splitlines() if (m := MARKER.search(line))]
    for marker in markers:
        print(f"{path}:0: contract-step accepted: {marker.group(1)}")
    if markers:
        return 0

    stripped = _strip_comments(text)
    violations = 0

    for lineno, line in enumerate(stripped.splitlines(), start=1):
        for pattern, reason in REASONS:
            for _ in pattern.finditer(line):
                print(f"{path}:{lineno}: {reason}")
                violations += 1

        # NOT NULL is only a violation on an ADD COLUMN without a DEFAULT:
        # inside CREATE TABLE it is fine (the table is empty), and a DEFAULT
        # lets the apply backfill existing rows. The clause runs to the end
        # of the line or the statement, whichever comes first.
        for match in ADD_COLUMN.finditer(line):
            clause = match.group("clause")
            if NOT_NULL.search(clause) and not HAS_DEFAULT.search(clause):
                print(f"{path}:{lineno}: NOT NULL on an ADD COLUMN with no "
                      "DEFAULT is not an expand step — it fails outright on "
                      "a table that already has rows; add a DEFAULT (or stay "
                      "nullable and backfill) behind a "
                      "`-- contract-step:` marker")
                violations += 1

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check .sql migrations for contract breaks (drops, "
                    "renames, NOT NULL without DEFAULT) that a rolling "
                    "deploy cannot survive, unless a "
                    "`-- contract-step:` marker says a reviewer approved it.")
    parser.add_argument(
        "paths", nargs="+",
        help="a .sql file, or a directory checked non-recursively for *.sql")
    args = parser.parse_args()

    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            found = sorted(path.glob("*.sql"))
            if not found:
                print(f"{path}: no .sql files here — nothing to check")
                continue
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            print(f"{path}: no such file or directory", file=sys.stderr)
            return 1

    violations = 0
    for path in files:
        violations += check_file(path)

    if violations:
        print(f"\n{violations} contract break(s) without a marker. A drop or "
              "rename belongs in a LATER deploy than the code reading the old "
              "shape, with `-- contract-step: <reason>` saying why now.")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())