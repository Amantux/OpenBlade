"""NFS export configuration rendering."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NfsExport:
    path: Path
    client: str = "*"
    read_only: bool = True

    def render(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{self.path} {self.client}({mode},sync,no_subtree_check)"
