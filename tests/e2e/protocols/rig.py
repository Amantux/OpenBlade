"""Protocol rig harness: render configs from NasShare records, stage the export.

`python -m tests.e2e.protocols.rig` prepares deploy/nas-protocols/state/ (used
by `make protocols-up`). Offline files are stubs handled by the rig's hydrator
shim (deploy/nas-protocols/hydrator_shim.py); the real hydrator is FUSE.
"""

import json
import os
import socket
import subprocess
from pathlib import Path

from openblade.catalog.models import NasShare
from openblade.nas.export_render import nfs_export_from, samba_share_from
from openblade.nas.nfs import render_ganesha_conf
from openblade.nas.samba import render_smb_conf

ROOT = Path(__file__).resolve().parents[3]
RIG = ROOT / "deploy" / "nas-protocols"
COMPOSE = ["docker", "compose", "-f", str(RIG / "docker-compose.yml")]
STATE = RIG / "state"
STUB_MAGIC = b"OPENBLADE-OFFLINE-STUB\n"
PAYLOAD = bytes(range(256)) * 4096  # 1 MiB, position-identifiable
DEEP_PARTS = ["d" * 200] * 19  # ~3.8 KiB relative path, under PATH_MAX 4096
LONG_NAME = "n" * 251 + ".bin"  # 255 bytes
UNICODE_NAME = "réсумé-日本語-🎞.txt"
# (scenario, stub mode) per protocol; each test gets its own stub.
OFFLINE = {
    "blocking": "delay:2",
    "timeout": "never",
    "tape_error": "tape_error",
    "concurrent": "delay:2",
    "evict": "delay:0",
    "restart": "delay:4",
}

SHARES = [
    NasShare(
        path="/rig/export/ro",
        name="ro",
        share_type="smb+nfs",
        config_json=json.dumps({"pseudo": "/ro"}),
    ),
    NasShare(
        path="/rig/export/rw",
        name="rw",
        share_type="smb+nfs",
        config_json=json.dumps({"read_only": False, "case_sensitive": True, "pseudo": "/rw"}),
    ),
]


RESTAGE = """
import json, os, sys, glob
root = '/rig/export/rw'
offline = json.loads(sys.argv[1]); magic = sys.argv[2].encode()
ctl = os.path.join(root, '.ob-control')
for proto in ('smb', 'nfs'):
    for scenario, mode in offline.items():
        rel = f'offline/{proto}-{scenario}.bin'
        tmp = os.path.join(root, rel + '.restage')
        with open(tmp, 'wb') as f:
            f.write(magic + json.dumps({'state': 'offline_on_tape', 'mode': mode}).encode())
        os.chmod(tmp, 0o666)
        os.replace(tmp, os.path.join(root, rel))
for stale in glob.glob(os.path.join(ctl, 'offline', '*.req')) + glob.glob(os.path.join(ctl, 'offline', '*.err')):
    os.unlink(stale)
open(os.path.join(ctl, 'hydration.log'), 'w').close()
print('restaged')
"""


def restage_snippet_args() -> list[str]:
    """argv for RESTAGE: reset every offline stub in place (new inode, same path).

    Used when a running rig is reused so an earlier run's hydrated files, stale
    recall requests and log lines cannot leak into the next session.
    """
    return [json.dumps(OFFLINE), STUB_MAGIC.decode()]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def compose_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("OB_SMB_PORT", str(free_port()))
    env.setdefault("OB_NFS_PORT", str(free_port()))
    return env


def prepare() -> None:
    """Render configs through openblade.nas and stage a fresh export tree."""
    export = STATE / "export"
    if export.exists():  # container-created files are root-owned: clean in-container
        subprocess.run(  # argv list, shell=False
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{STATE}:/s",
                "debian:bookworm-slim",
                "rm",
                "-rf",
                "/s/export",
            ],
            check=True,
        )
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "smb.conf").write_text(render_smb_conf(samba_share_from(s) for s in SHARES))
    exports = [nfs_export_from(s, export_id=i + 1) for i, s in enumerate(SHARES)]
    (STATE / "ganesha.conf").write_text(render_ganesha_conf(exports))
    ro, rw = export / "ro", export / "rw"
    deep = ro.joinpath(*DEEP_PARTS)
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("deep")
    (rw / "offline").mkdir(parents=True)
    (rw / ".ob-tape" / "offline").mkdir(parents=True)
    # Clients drop recall requests here; must exist and be guest-writable before
    # the shim starts (it only creates .ob-control itself, root-owned 0755).
    (rw / ".ob-control" / "offline").mkdir(parents=True)
    (ro / "readme.txt").write_text("hello openblade\n")
    (ro / "data.bin").write_bytes(PAYLOAD)
    (ro / UNICODE_NAME).write_text("unicode")
    (ro / LONG_NAME).write_text("long")
    (ro / "mode640.txt").write_text("acl")
    (ro / "mode640.txt").chmod(0o640)
    for proto in ("smb", "nfs"):
        for scenario, mode in OFFLINE.items():
            rel = f"offline/{proto}-{scenario}.bin"
            (rw / rel).write_bytes(
                STUB_MAGIC + json.dumps({"state": "offline_on_tape", "mode": mode}).encode()
            )
            (rw / ".ob-tape" / rel).write_bytes(PAYLOAD)
    os.chmod(export, 0o777)
    for path in [rw, *rw.rglob("*")]:
        path.chmod(0o777 if path.is_dir() else 0o666)


if __name__ == "__main__":
    prepare()
