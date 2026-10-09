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


def test_nfs_acl_mapping() -> None:
    pytest.skip(
        "NFS ACL mapping needs nfs4_getfacl (nfs4-acl-tools) and Ganesha NFSv4 ACL support; not in rig"
    )
