"""Each backend image's CMD — what Kubernetes actually runs — would start.

Both compose stacks override every backend service's command with
`fastapi dev`, so a Dockerfile CMD is exercised nowhere before a deploy. One
once passed `fastapi run` an option it does not have; every new pod exited 2
and crash-looped on staging, and the next promote would have taken eesoc.com
down with it.

So each CMD is parsed out of its Dockerfile and run with `--help` appended
inside the test stack's container for that service. click rejects an unknown
option while parsing (exit 2) before it gets as far as printing help (exit 0),
so this checks every option without starting a server. The `module:app` target
is imported separately.

NEEDS THE COMPOSE TEST STACK. `union` is not in it — the stub stands in — but
its image pins the same server as the api's, so its CMD is checked in the api
container; that the pins match is asserted, so it stops being true loudly.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Service whose Dockerfile is checked -> test-stack container to check it in.
CHECKED_IN = {"api": "api", "database": "database", "union": "api"}


def _cmd(service: str) -> list[str]:
    lines = [
        line for line in (REPO_ROOT / "backend" / service / "Dockerfile").read_text().splitlines()
        if line.startswith("CMD ")
    ]
    assert len(lines) == 1, f"expected one CMD in backend/{service}/Dockerfile"
    # The exec form: a JSON array, which is the only form these images use.
    return json.loads(lines[0][len("CMD "):])


def _fastapi_pin(service: str) -> str | None:
    for line in (REPO_ROOT / "backend" / service / "requirements.txt").read_text().splitlines():
        if line.strip().startswith("fastapi"):
            return line.strip()
    return None


def test_union_installs_the_same_server_as_the_api():
    assert _fastapi_pin("union") == _fastapi_pin("api")


@pytest.mark.parametrize("service", sorted(CHECKED_IN))
def test_every_cmd_option_is_accepted(compose_exec, service):
    # compose_exec raises with the container's stderr on a non-zero exit, which
    # names the offending option ("No such option: ...").
    compose_exec(CHECKED_IN[service], *_cmd(service), "--help")


@pytest.mark.parametrize("service", ["api", "database"])
def test_cmd_app_target_imports(compose_exec, service):
    target = next(arg for arg in _cmd(service) if ":" in arg and not arg.startswith("-"))
    module, attribute = target.split(":", 1)
    compose_exec(
        service, "python", "-c",
        f"import {module}; assert hasattr({module}, {attribute!r})",
    )
