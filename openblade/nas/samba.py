"""Samba share configuration rendering."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SambaShare:
    name: str
    path: Path
    read_only: bool = True

    def render(self) -> str:
        writable = "no" if self.read_only else "yes"
        return f"[{self.name}]\n  path = {self.path}\n  writeable = {writable}\n"
