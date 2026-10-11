"""Map catalog ``NasShare`` records onto Samba/NFS export definitions.

``NasShare.config_json`` keys honoured: ``read_only`` (bool, default True),
``case_sensitive`` (bool, default False), ``client`` (NFS client spec, default
``*``), ``export_id`` (int) and ``pseudo`` (NFSv4 pseudo path).
"""

import json
from pathlib import Path
from typing import Any

from openblade.catalog.models import NasShare
from openblade.nas.nfs import NfsExport
from openblade.nas.samba import SambaShare


class InvalidShareConfigError(ValueError):
    """``NasShare.config_json`` is not a JSON object."""


def _config(share: NasShare) -> dict[str, Any]:
    try:
        raw = json.loads(share.config_json or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidShareConfigError(f"share {share.name!r}: config_json is not JSON") from exc
    if not isinstance(raw, dict):
        raise InvalidShareConfigError(f"share {share.name!r}: config_json must be an object")
    return raw


def samba_share_from(share: NasShare) -> SambaShare:
    cfg = _config(share)
    return SambaShare(
        name=share.name,
        path=Path(share.path),
        read_only=bool(cfg.get("read_only", True)),
        case_sensitive=bool(cfg.get("case_sensitive", False)),
    )


def nfs_export_from(share: NasShare, export_id: int = 1) -> NfsExport:
    cfg = _config(share)
    return NfsExport(
        path=Path(share.path),
        client=str(cfg.get("client", "*")),
        read_only=bool(cfg.get("read_only", True)),
        export_id=int(cfg.get("export_id", export_id)),
        pseudo=cfg.get("pseudo"),
    )
