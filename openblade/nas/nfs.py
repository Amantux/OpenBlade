"""NFS export configuration rendering."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NfsExport:
    path: Path
    client: str = "*"
    read_only: bool = True
    export_id: int = 1
    pseudo: str | None = None

    def render(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{self.path} {self.client}({mode},sync,no_subtree_check)"

    def render_ganesha(self) -> str:
        """Render this export as an NFS-Ganesha ``EXPORT`` block."""
        access = "RO" if self.read_only else "RW"
        pseudo = self.pseudo or str(self.path)
        return (
            "EXPORT {\n"
            f"  Export_Id = {self.export_id};\n"
            f'  Path = "{self.path}";\n'
            f'  Pseudo = "{pseudo}";\n'
            "  Protocols = 4;\n"
            "  Transports = TCP;\n"
            f"  Access_Type = {access};\n"
            "  Squash = No_Root_Squash;\n"
            "  SecType = sys;\n"
            f'  CLIENT {{ Clients = "{self.client}"; Access_Type = {access}; }}\n'
            "  FSAL { Name = VFS; }\n"
            "}\n"
        )


def render_exports(exports: Iterable[NfsExport]) -> str:
    """Render a kernel-nfsd ``/etc/exports`` file."""
    return "".join(export.render() + "\n" for export in exports)


def render_ganesha_conf(exports: Iterable[NfsExport]) -> str:
    """Render a complete NFS-Ganesha config (NFSv4 only, VFS FSAL)."""
    header = (
        "NFS_CORE_PARAM {\n"
        "  Protocols = 4;\n"
        "  NFS_Port = 2049;\n"
        "}\n"
        "NFSV4 {\n"
        "  Grace_Period = 5;\n"
        "  Lease_Lifetime = 5;\n"
        "}\n"
    )
    return header + "".join(export.render_ganesha() for export in exports)
