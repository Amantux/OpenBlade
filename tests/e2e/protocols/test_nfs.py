"""NFSv4 (NFS-Ganesha, kernel nfs4 client) protocol scenarios."""

import pytest

from tests.e2e.protocols.conftest import Rig
from tests.e2e.protocols.scenarios import *  # noqa: F403 -- re-collect the shared scenarios bound to proto=nfs

pytestmark = pytest.mark.protocols


@pytest.fixture
def proto() -> str:
    return "nfs"


def test_nfs_is_case_sensitive(mounted: Rig) -> None:
    assert mounted.sh("cat /mnt/nfs/ro/README.TXT").returncode != 0


def test_nfs_posix_lock_granted_by_server(mounted: Rig) -> None:
    code = "import fcntl;f=open('/mnt/nfs/rw/lock.bin','wb');fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB,10);print('LOCKED')"
    assert mounted.py(code).stdout.strip() == "LOCKED"


def test_nfs_acl_mapping(mounted: Rig) -> None:
    """Access control on the rw export as the rig's Ganesha actually provides it.

    Limit (verified on the pinned debian:bookworm nfs-ganesha 4.3 + nfs-ganesha-vfs build):
    the VFS FSAL does not advertise the NFSv4 ``acl`` attribute, even with
    ``Disable_ACL = false`` on the export and a fresh client mount -- ``nfs4_getfacl``
    reports "Operation to request attribute not supported" and ``nfs4_setfacl`` fails
    with "Failed to instantiate ACL". Ganesha 4.3 has no ``Allow_ACL`` key. So NFSv4 ACEs
    cannot be set or read through this server; the test pins that limit (it fails, and
    must be rewritten as a set/get round-trip, if a future image starts storing ACLs) and
    asserts that POSIX mode bits are enforced server-side for a second uid instead.
    """
    f = "/mnt/nfs/rw/acl.bin"
    assert mounted.sh(f"echo hi > {f}").returncode == 0
    acl = mounted.sh(f"nfs4_getfacl {f}; nfs4_setfacl -a A::OWNER@:rwatTnNcCy {f}")
    assert "attribute not supported" in acl.stdout + acl.stderr, acl.stdout + acl.stderr
    assert acl.returncode != 0, "nfs4_setfacl succeeded: server now stores ACLs, extend this test"
    assert mounted.sh(f"chmod 600 {f} && runuser -u nobody -- cat {f}").returncode != 0
    allowed = mounted.sh(f"chmod 644 {f} && runuser -u nobody -- cat {f}")
    assert allowed.returncode == 0 and allowed.stdout.strip() == "hi", allowed.stderr
