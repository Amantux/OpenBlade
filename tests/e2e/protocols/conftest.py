"""Fixtures for the containerised SMB/NFS protocol rig (marker: protocols).

Path taken for NFS (and SMB file I/O): KERNEL mounts (cifs vers=3.0, nfs4)
inside the privileged `client` container; checks run there via
`docker compose exec`. Without docker every test here SKIPS.
"""

import shutil
import subprocess
from collections.abc import Iterator

import pytest

# Before any test module imports scenarios, so its bare asserts show values.
pytest.register_assert_rewrite("tests.e2e.protocols.scenarios")

from tests.e2e.protocols import rig  # noqa: E402 -- must follow register_assert_rewrite

MOUNTS = {
    "smb": "mount -t cifs //samba/{s} /mnt/smb/{s} -o guest,vers=3.0,cache=none,actimeo=0",
    "nfs": "mount -t nfs4 -o port=2049,noac,lookupcache=none,timeo=50 nfs-ganesha:/{s} /mnt/nfs/{s}",
}


class Rig:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    def compose(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # argv list, shell=False
            [*rig.COMPOSE, *args], env=self.env, capture_output=True, text=True, timeout=timeout
        )

    def sh(self, script: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        """Run a shell snippet inside the client container."""
        return self.compose("exec", "-T", "client", "sh", "-c", script, timeout=timeout)

    def py(self, code: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return self.compose("exec", "-T", "client", "python3", "-c", code, timeout=timeout)

    def mount(self, proto: str) -> None:
        for share in ("ro", "rw"):
            cmd = MOUNTS[proto].format(s=share)
            res = self.sh(
                f"mkdir -p /mnt/{proto}/{share}; mountpoint -q /mnt/{proto}/{share} || {cmd}"
            )
            if res.returncode != 0:
                pytest.skip(
                    f"kernel {proto} mount refused in client container: {res.stderr.strip()[:200]}"
                )


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=20)  # argv list
    except (OSError, subprocess.TimeoutExpired):
        return False
    return info.returncode == 0


@pytest.fixture(scope="session")
def protocol_rig() -> Iterator[Rig]:
    if not _docker_ok():
        pytest.skip("docker daemon not available: protocol rig cannot start")
    env = rig.compose_env()
    handle = Rig(env)
    running = handle.compose("ps", "--status", "running", "-q", "client")
    if not running.stdout.strip():
        rig.prepare()
        up = handle.compose("up", "-d", "--build", "--wait", timeout=600)
        if up.returncode != 0:
            pytest.skip(
                f"protocol rig failed to start (docker compose up): {up.stderr.strip()[-300:]}"
            )
    yield handle


@pytest.fixture
def mounted(protocol_rig: Rig, proto: str) -> Rig:
    protocol_rig.mount(proto)
    return protocol_rig
