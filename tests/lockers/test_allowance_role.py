"""Free locker allowances are a superuser's alone (api/utils/roles.py).

A locker is otherwise paid for, so an allowance gives away the society's money.
If another role is ever meant to hold it, this is the test to change — on
purpose, not as a side effect of reshuffling the roles table.
"""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from utils.roles import ROLES, capabilities  # noqa: E402


def test_only_a_superuser_may_give_free_lockers():
    assert [r for r in ROLES if "locker_allowances" in capabilities(r)] == ["superuser"]
