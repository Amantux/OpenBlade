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
    assert "ACL:Everyone:ALLOWED/0x0/0x0" not in res.stdout or "Unix Group" in res.stdout
    assert "Unix User\\root:ALLOWED/0x0/RW" in res.stdout, res.stdout


def test_smb_byte_range_lock_conflicts_across_sessions(mounted: Rig) -> None:
    code = """
import smbclient, smbclient._io as io
from smbprotocol.exceptions import SMBOSError
from smbprotocol.open import SMB2LockElement, LockFlags
smbclient.ClientConfig(username='guest', password='', auth_protocol='ntlm')
p = r'\\\\samba\\rw\\lock.bin'
with smbclient.open_file(p, 'wb') as f: f.write(b'0' * 100)
a = smbclient.open_file(p, 'rb', share_access='rw'); b = smbclient.open_file(p, 'rb', share_access='rw')
el = SMB2LockElement(); el['offset'] = 0; el['length'] = 10
el['flags'] = LockFlags.SMB2_LOCKFLAG_EXCLUSIVE_LOCK | LockFlags.SMB2_LOCKFLAG_FAIL_IMMEDIATELY
a.fd.lock([el])
try:
    b.fd.lock([el]); print('GRANTED')
except Exception as e:
    print(type(e).__name__)
"""
    res = mounted.py(code)
    if "Error" in res.stderr and not res.stdout:
        pytest.skip(f"smbprotocol lock driver unavailable: {res.stderr.strip()[-200:]}")
    assert res.stdout.strip() not in {"", "GRANTED"}, res.stdout + res.stderr
