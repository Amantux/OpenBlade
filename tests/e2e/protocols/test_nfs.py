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
    """A real NFSv4 ACL is enforced server-side, beyond what the mode bits say.

    The rig's Ganesha (Fedora 41 nfs-ganesha 5.9, VFS FSAL) maps NFSv4 ACLs onto POSIX
    ACLs. A DENY ACE for a named user (``nobody``, uid 65534 -- numeric principals, the
    kernel client's default without idmapping) is stored as a ``user:65534:---`` POSIX
    entry, so ``nobody`` is refused a read that mode 644 alone would allow, while the
    owner keeps reading. The mapping is lossy (POSIX has no DENY and no
    ``n``/``N`` named-attribute bits), so only the deny ACE itself is asserted to
    round-trip, not the whole ACL text. Mode-bit enforcement for a second uid is kept.
    """
    f = "/mnt/nfs/rw/acl.bin"
    deny_ace = "D::65534:r"
    assert mounted.sh(f"rm -f {f} && echo hi > {f} && chmod 644 {f}").returncode == 0
    assert mounted.sh(f"runuser -u nobody -- cat {f}").returncode == 0

    set_ = mounted.sh(f"nfs4_setfacl -a {deny_ace} {f}")
    assert set_.returncode == 0, set_.stdout + set_.stderr
    assert deny_ace in mounted.sh(f"nfs4_getfacl {f}").stdout.splitlines()
    assert mounted.sh(f"stat -c %a {f}").stdout.strip() == "644"
    assert mounted.sh(f"runuser -u nobody -- cat {f}").returncode != 0
    owner = mounted.sh(f"cat {f}")
    assert owner.returncode == 0 and owner.stdout.strip() == "hi", owner.stderr

    unset = mounted.sh(f"nfs4_setfacl -x {deny_ace} {f}")
    assert unset.returncode == 0, unset.stdout + unset.stderr
    assert deny_ace not in mounted.sh(f"nfs4_getfacl {f}").stdout.splitlines()
    readable = mounted.sh(f"runuser -u nobody -- cat {f}")
    assert readable.returncode == 0 and readable.stdout.strip() == "hi", readable.stderr

    assert mounted.sh(f"rm -f {f} && echo hi > {f}").returncode == 0
    assert mounted.sh(f"chmod 600 {f} && runuser -u nobody -- cat {f}").returncode != 0
    allowed = mounted.sh(f"chmod 644 {f} && runuser -u nobody -- cat {f}")
    assert allowed.returncode == 0 and allowed.stdout.strip() == "hi", allowed.stderr
