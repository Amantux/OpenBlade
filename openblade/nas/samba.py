"""Samba share configuration rendering."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SambaShare:
    name: str
    path: Path
    read_only: bool = True
    case_sensitive: bool = False
    guest_ok: bool = True

    def render(self) -> str:
        writable = "no" if self.read_only else "yes"
        lines = [
            f"[{self.name}]",
            f"  path = {self.path}",
            f"  writeable = {writable}",
            f"  case sensitive = {'yes' if self.case_sensitive else 'no'}",
            f"  guest ok = {'yes' if self.guest_ok else 'no'}",
            "  browseable = yes",
        ]
        return "\n".join(lines) + "\n"


def render_smb_conf(shares: Iterable[SambaShare], *, force_user: str = "root") -> str:
    """Render a complete ``smb.conf`` for ``shares``.

    The global section serves guest SMB2/3 only (no SMB1) with POSIX locking on
    so byte-range locks are visible across protocols.
    """
    header = (
        "[global]\n"
        "  workgroup = OPENBLADE\n"
        "  server role = standalone server\n"
        "  server min protocol = SMB2_10\n"
        "  map to guest = Bad User\n"
        f"  guest account = {force_user}\n"
        "  load printers = no\n"
        "  disable spoolss = yes\n"
        "  posix locking = yes\n"
        "  unix extensions = no\n"
        "  log file = /var/log/samba/%m.log\n"
    )
    return header + "".join("\n" + share.render() for share in shares)
