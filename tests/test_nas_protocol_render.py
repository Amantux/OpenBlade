"""Unit tests for the SMB/NFS config renderers (no docker; not marked protocols)."""

from pathlib import Path

from openblade.nas.nfs import NfsExport, render_exports, render_ganesha_conf
from openblade.nas.samba import SambaShare, render_smb_conf


def test_ganesha_client_list_is_unquoted() -> None:
    # Regression: Ganesha rejects `Clients = "*"` ("Expected a client") and then
    # silently drops the whole EXPORT, so NFS mounts fail with ENOENT.
    block = NfsExport(Path("/rig/export/ro"), pseudo="/ro").render_ganesha()

    assert "Clients = *;" in block
    assert '"*"' not in block


def test_ganesha_conf_renders_one_export_per_share_with_access_type() -> None:
    conf = render_ganesha_conf(
        [
            NfsExport(Path("/e/ro"), export_id=1, pseudo="/ro"),
            NfsExport(Path("/e/rw"), export_id=2, pseudo="/rw", read_only=False),
        ]
    )

    assert conf.count("EXPORT {") == 2
    assert 'Pseudo = "/ro";' in conf and "Access_Type = RO;" in conf
    assert 'Pseudo = "/rw";' in conf and "Access_Type = RW;" in conf
    assert "Protocols = 4;" in conf


def test_ganesha_pseudo_defaults_to_path() -> None:
    assert 'Pseudo = "/data/x";' in NfsExport(Path("/data/x")).render_ganesha()


def test_kernel_exports_line_reflects_read_only() -> None:
    out = render_exports([NfsExport(Path("/a")), NfsExport(Path("/b"), read_only=False)])

    assert out == "/a *(ro,sync,no_subtree_check)\n/b *(rw,sync,no_subtree_check)\n"


def test_smb_conf_maps_share_flags() -> None:
    conf = render_smb_conf(
        [
            SambaShare("ro", Path("/e/ro")),
            SambaShare("rw", Path("/e/rw"), read_only=False, case_sensitive=True),
        ]
    )

    ro, rw = conf.split("[ro]", 1)[1].split("[rw]", 1)
    assert "writeable = no" in ro and "case sensitive = no" in ro
    assert "writeable = yes" in rw and "case sensitive = yes" in rw
