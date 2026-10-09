"""Scenarios shared by test_smb.py and test_nfs.py (each binds the `proto` fixture).

All file I/O runs inside the client container against the kernel mount at
/mnt/<proto>/{ro,rw}. Hydration uses the rig shim (not FUSE): a client requests
recall by creating .ob-control/<rel>.req through the share, then polls.
"""

import time

import pytest

from tests.e2e.protocols import rig
from tests.e2e.protocols.conftest import Rig

pytestmark = pytest.mark.protocols

POLL = """
import sys, time
p, deadline = sys.argv[1], time.time() + float(sys.argv[2])
try:
    open(p.replace('/offline/', '/.ob-control/offline/') + '.req', 'w').close()
except FileNotFoundError:
    pass  # SMB: shim unlinked a concurrent client's .req mid-create; recall already queued
while time.time() < deadline:
    data = open(p, 'rb').read()
    if not data.startswith(b'OPENBLADE-OFFLINE-STUB'):
        print('HYDRATED', len(data)); sys.exit(0)
    time.sleep(0.2)
print('HYDRATION_TIMEOUT'); sys.exit(3)
"""


def _poll(r: Rig, proto: str, scenario: str, timeout: float) -> tuple[int, str]:
    path = f"/mnt/{proto}/rw/offline/{proto}-{scenario}.bin"
    res = r.compose(
        "exec", "-T", "client", "python3", "-c", POLL, path, str(timeout), timeout=timeout + 30
    )
    return res.returncode, res.stdout


def _hydration_log(r: Rig) -> str:
    return (rig.STATE / "export" / "rw" / ".ob-control" / "hydration.log").read_text()


def test_listing_shows_staged_files(mounted: Rig, proto: str) -> None:
    res = mounted.sh(f"ls /mnt/{proto}/ro")
    assert {"readme.txt", "data.bin", rig.UNICODE_NAME} <= set(res.stdout.split("\n"))


def test_open_read_seek_partial_range(mounted: Rig, proto: str) -> None:
    code = (
        f"f=open('/mnt/{proto}/ro/data.bin','rb'); f.seek(1000); print(f.read(4).hex()); f.close()"
    )
    assert mounted.py(code).stdout.strip() == rig.PAYLOAD[1000:1004].hex()


def test_rename_delete_on_rw_share_succeeds(mounted: Rig, proto: str) -> None:
    d = f"/mnt/{proto}/rw"
    res = mounted.sh(
        f"echo x > {d}/a.txt && mv {d}/a.txt {d}/b.txt && rm {d}/b.txt && ! test -e {d}/b.txt"
    )
    assert res.returncode == 0, res.stderr


def test_write_on_ro_share_is_refused(mounted: Rig, proto: str) -> None:
    res = mounted.sh(f"mv /mnt/{proto}/ro/readme.txt /mnt/{proto}/ro/x.txt")
    assert res.returncode != 0
    assert mounted.sh(f"test -e /mnt/{proto}/ro/readme.txt").returncode == 0


def test_unicode_long_name_and_deep_path_readable(mounted: Rig, proto: str) -> None:
    deep = "/".join(rig.DEEP_PARTS)
    res = mounted.sh(
        f"cat '/mnt/{proto}/ro/{rig.UNICODE_NAME}' /mnt/{proto}/ro/{rig.LONG_NAME} /mnt/{proto}/ro/{deep}/leaf.txt"
    )
    assert res.stdout == "unicodelongdeep", res.stderr


def test_sparse_write_past_end_keeps_apparent_size(mounted: Rig, proto: str) -> None:
    name = f"sparse-{proto}.bin"
    mounted.py(f"f=open('/mnt/{proto}/rw/{name}','wb'); f.seek(64<<20); f.write(b'z'); f.close()")
    st = (rig.STATE / "export" / "rw" / name).stat()
    assert st.st_size == (64 << 20) + 1
    assert st.st_blocks * 512 < st.st_size // 2, f"server allocated {st.st_blocks * 512} bytes"


def test_interrupted_reader_then_reread_ok(mounted: Rig, proto: str) -> None:
    reader = f"while true; do cat /mnt/{proto}/ro/data.bin > /dev/null; done"
    mounted.sh(
        f"nohup sh -c '{reader}' >/dev/null 2>&1 & sleep 1; pkill -9 -f 'data.bin > /dev/null' ; true"
    )
    code = f"import hashlib;print(hashlib.sha256(open('/mnt/{proto}/ro/data.bin','rb').read()).hexdigest())"
    import hashlib

    assert mounted.py(code).stdout.strip() == hashlib.sha256(rig.PAYLOAD).hexdigest()


def test_hydration_blocks_until_shim_recalls(mounted: Rig, proto: str) -> None:
    start = time.monotonic()
    rc, out = _poll(mounted, proto, "blocking", 20)
    assert rc == 0 and f"HYDRATED {len(rig.PAYLOAD)}" in out
    assert time.monotonic() - start >= 2  # stub mode delay:2


def test_hydration_never_recalled_times_out(mounted: Rig, proto: str) -> None:
    rc, out = _poll(mounted, proto, "timeout", 3)
    assert (rc, out.strip()) == (3, "HYDRATION_TIMEOUT")


def test_offline_tape_error_is_reported(mounted: Rig, proto: str) -> None:
    rc, _ = _poll(mounted, proto, "tape_error", 3)
    err = rig.STATE / "export" / "rw" / ".ob-control" / "offline" / f"{proto}-tape_error.bin.err"
    assert rc == 3 and err.read_text() == "OFFLINE_TAPE_UNAVAILABLE"


def test_concurrent_opens_trigger_one_hydration(mounted: Rig, proto: str) -> None:
    path = f"/mnt/{proto}/rw/offline/{proto}-concurrent.bin"
    script = f'for i in 1 2 3 4 5 6 7 8; do python3 -c "$P" {path} 20 & done; wait'
    res = mounted.compose("exec", "-T", "-e", f"P={POLL}", "client", "sh", "-c", script, timeout=60)
    assert res.stdout.count("HYDRATED") == 8
    assert _hydration_log(mounted).count(f"recall offline/{proto}-concurrent.bin") == 1


def test_eviction_while_open_keeps_old_bytes_or_estale(mounted: Rig, proto: str) -> None:
    """Shim-style atomic replace (new inode) while a handle is open."""
    rel = f"offline/{proto}-evict.bin"
    assert _poll(mounted, proto, "evict", 20)[0] == 0
    code = f"""
import os, time, errno
f = open('/mnt/{proto}/rw/{rel}', 'rb'); first = f.read(4)
srv = '/rig/export/rw/{rel}'  # server-side eviction, as the real evictor would
open(srv + '.tmp', 'wb').write(b'OPENBLADE-OFFLINE-STUB\\n{{}}'); os.replace(srv + '.tmp', srv)
time.sleep(1)
try:
    f.seek(0); print('OLD' if f.read(4) == first else 'NEW')
except OSError as e:
    print(errno.errorcode[e.errno])
"""
    proc = mounted.compose("exec", "-T", "client", "python3", "-c", code, timeout=30)
    assert proc.stdout.strip() in {"OLD", "ESTALE"}, proc.stdout + proc.stderr


def test_server_restart_during_hydration_completes(mounted: Rig, proto: str) -> None:
    service = "samba" if proto == "smb" else "nfs-ganesha"
    path = f"/mnt/{proto}/rw/offline/{proto}-restart.bin"
    mounted.compose(
        "exec", "-T", "client", "touch", path.replace("/offline/", "/.ob-control/offline/") + ".req"
    )
    assert mounted.compose("restart", service, timeout=90).returncode == 0
    rc, out = _poll(mounted, proto, "restart", 60)
    assert rc == 0 and "HYDRATED" in out
