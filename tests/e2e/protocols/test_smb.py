"""SMB (Samba, kernel cifs vers=3.0) protocol scenarios."""

import pytest

from tests.e2e.protocols.conftest import Rig
from tests.e2e.protocols.scenarios import *  # noqa: F403 -- re-collect the shared scenarios bound to proto=smb

pytestmark = pytest.mark.protocols


@pytest.fixture
def proto() -> str:
    return "smb"


def test_smb_case_insensitive_default_share(mounted: Rig) -> None:
    assert mounted.sh("cat /mnt/smb/ro/README.TXT").stdout == "hello openblade\n"


def test_smb_case_sensitive_share_keeps_distinct_names(mounted: Rig) -> None:
    res = mounted.sh(
        "cd /mnt/smb/rw && echo a > Case.txt && echo b > case.txt && cat Case.txt case.txt"
    )
    assert res.stdout == "a\nb\n", res.stderr


def test_smb_acl_maps_posix_mode(mounted: Rig) -> None:
    res = mounted.sh("smbcacls -N //samba/ro mode640.txt")
    assert res.returncode == 0, res.stderr
    # smbcacls prints "ACL:<sid-name>:ALLOWED/<flags>/<perms>". Mode 0640 must map
    # to owner=RW, group=R, Everyone=no rights. The owner's name is the server's
    # netbios name (container id), so match on the OWNER: line rather than a literal.
    lines = res.stdout.splitlines()
    owner = next(line.split(":", 1)[1] for line in lines if line.startswith("OWNER:"))
    assert f"ACL:{owner}:ALLOWED/0x0/RW" in lines, res.stdout
    assert "ACL:Unix Group\\root:ALLOWED/0x0/R" in lines, res.stdout
    assert "ACL:Everyone:ALLOWED/0x0/" in lines, res.stdout


# smbprotocol 1.15.0 has the SMB2_LOCK command code but no LOCK request/Open.lock(), so the
# snippet builds the [MS-SMB2] 2.2.26 request itself. Guest sessions cannot sign, hence
# require_signing=False and require_secure_negotiate=False.
_LOCK_SNIPPET = r"""
import struct
from collections import OrderedDict
import smbclient
from smbprotocol.exceptions import SMBResponseException
from smbprotocol.header import Commands
from smbprotocol.structure import BytesField, IntField, Structure

class LockReq(Structure):  # [MS-SMB2] 2.2.26; smbprotocol 1.15.0 has no LOCK request
    COMMAND = Commands.SMB2_LOCK
    def __init__(self):
        self.fields = OrderedDict([
            ("structure_size", IntField(size=2, default=48)),
            ("lock_count", IntField(size=2, default=1)),
            ("lock_sequence", IntField(size=4)),
            ("file_id", BytesField(size=16)),
            ("locks", BytesField(size=24)),
        ])
        super().__init__()

def lock(f, flags):
    o = getattr(f, "raw", f).fd
    req = LockReq(); req["file_id"] = o.file_id
    req["locks"] = struct.pack("<QQII", 0, 10, flags, 0)
    sid, tid = o.tree_connect.session.session_id, o.tree_connect.tree_connect_id
    try:
        o.connection.receive(o.connection.send(req, sid, tid)); return "GRANTED"
    except SMBResponseException as e:
        return "0x%08x" % e.status

EXCL_NOW, UNLOCK = 0x02 | 0x10, 0x04
p = r"\\samba\rw\lock.bin"

def session() -> dict:
    # Guest sessions cannot sign; smbclient requires signing by default.
    cache: dict = {}
    smbclient.register_session("samba", username="guest", password="", auth_protocol="ntlm", require_signing=False, connection_cache=cache)
    return cache

smbclient.ClientConfig(require_secure_negotiate=False)  # needs a signing key guests lack
ca, cb = session(), session()
with smbclient.open_file(p, "wb", connection_cache=ca) as f:
    f.write(b"0" * 100)
a = smbclient.open_file(p, "rb", share_access="rw", connection_cache=ca)
b = smbclient.open_file(p, "rb", share_access="rw", connection_cache=cb)
assert getattr(a, "raw", a).fd.tree_connect.session.session_id != getattr(b, "raw", b).fd.tree_connect.session.session_id
print(lock(a, EXCL_NOW), lock(b, EXCL_NOW), lock(a, UNLOCK), lock(b, EXCL_NOW), lock(b, UNLOCK))
"""


def test_smb_byte_range_lock_conflicts_across_sessions(mounted: Rig) -> None:
    """A conflicting exclusive lock from a second SMB session is refused until released."""
    res = mounted.py(_LOCK_SNIPPET)
    assert res.returncode == 0, res.stderr[-800:]
    # a locks, b conflicts (STATUS_LOCK_NOT_GRANTED), a unlocks, b now locks, b unlocks.
    assert res.stdout.split() == ["GRANTED", "0xc0000055", "GRANTED", "GRANTED", "GRANTED"], (
        res.stdout
    )
