"""Regression tests for the CLI's backend selection and mock-state handling.

Found during the first real-data campaign (docs/runbooks/real-data-campaign.md):
with ``OPENBLADE_BACKEND=real`` exported and a live library attached,
``openblade inventory`` printed a simulated 50-slot library with ``VOL0nnL9``
barcodes and no warning, because the CLI built its own ``OpenBladeConfig()``
instead of calling ``load_config()``.
"""

from __future__ import annotations

import json

import pytest

from openblade.cli import main as cli_main
from openblade.config import BackendMode


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENBLADE_BACKEND", "OPENBLADE_REAL_HARDWARE_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_default_config_defaults_to_mock() -> None:
    assert cli_main._default_config().backend is BackendMode.MOCK


def test_default_config_honours_real_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug: this used to come back MOCK, silently simulating a real library."""
    monkeypatch.setenv("OPENBLADE_BACKEND", "real")
    monkeypatch.setenv("OPENBLADE_REAL_HARDWARE_ENABLED", "true")

    config = cli_main._default_config()

    assert config.backend is BackendMode.REAL
    assert config.real_hardware_enabled is True


def test_default_config_keeps_home_relative_paths() -> None:
    """Honouring the environment must not move the CLI's default state location."""
    config = cli_main._default_config()

    assert config.db_url == f"sqlite:///{cli_main._DB_PATH}"
    assert config.cache_dir == str(cli_main._STATE_DIR / "cache")
    assert config.restore_dir == str(cli_main._STATE_DIR / "restore")
    assert config.staging_dir == str(cli_main._STATE_DIR / "staging")


def test_mock_config_pins_mock_even_under_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`openblade mock init` must never reach for a real changer."""
    monkeypatch.setenv("OPENBLADE_BACKEND", "real")
    monkeypatch.setenv("OPENBLADE_REAL_HARDWARE_ENABLED", "true")

    config = cli_main._mock_config()

    assert config.backend is BackendMode.MOCK
    assert config.real_hardware_enabled is False


class _FakeContext:
    def __init__(self, library: object, ltfs: object) -> None:
        self.library = library
        self.ltfs = ltfs


class _NotAMock:
    pass


def test_is_mock_rejects_non_simulator_backends() -> None:
    from openblade.simulator.library import MockLibraryBackend
    from openblade.simulator.ltfs_volume import MockLTFSBackend

    library = MockLibraryBackend(num_slots=4, num_drives=1)
    assert cli_main._is_mock(_FakeContext(library, MockLTFSBackend(library))) is True
    assert cli_main._is_mock(_FakeContext(_NotAMock(), _NotAMock())) is False


def test_load_state_leaves_a_real_context_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A real library is the state; mock_state.json must never shadow it."""
    state_path = tmp_path / "mock_state.json"
    state_path.write_text(json.dumps({"library": {"num_slots": 99}, "ltfs": {}}))
    monkeypatch.setattr(cli_main, "_STATE_PATH", state_path)
    monkeypatch.setattr(cli_main, "_STATE_DIR", tmp_path)

    real_library = _NotAMock()
    context = _FakeContext(real_library, _NotAMock())

    assert cli_main._load_state(context) is context
    assert context.library is real_library
    # ...and the file is neither rewritten nor moved aside.
    assert json.loads(state_path.read_text())["library"]["num_slots"] == 99


def test_save_state_is_a_no_op_for_a_real_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    state_path = tmp_path / "mock_state.json"
    monkeypatch.setattr(cli_main, "_STATE_PATH", state_path)
    monkeypatch.setattr(cli_main, "_STATE_DIR", tmp_path)

    cli_main._save_state(_FakeContext(_NotAMock(), _NotAMock()))

    assert not state_path.exists()


def test_load_state_recovers_from_an_incompatible_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An older release's mock_state.json used to traceback on every command."""
    from openblade.simulator.library import MockLibraryBackend
    from openblade.simulator.ltfs_volume import MockLTFSBackend

    state_path = tmp_path / "mock_state.json"
    # The shape written by the previous CLI: slots as a list, no num_slots key.
    state_path.write_text(
        json.dumps(
            {
                "library": {"library_id": "mock-i3-001", "slots": [{"slot_id": 1, "barcode": None}]},
                "ltfs": {"capacity_bytes": 1024, "tapes": {}},
            }
        )
    )
    monkeypatch.setattr(cli_main, "_STATE_PATH", state_path)
    monkeypatch.setattr(cli_main, "_STATE_DIR", tmp_path)

    library = MockLibraryBackend(num_slots=4, num_drives=1)
    context = _FakeContext(library, MockLTFSBackend(library))

    result = cli_main._load_state(context)  # must not raise

    assert result is context
    assert (tmp_path / "mock_state.json.stale").exists()
    assert state_path.exists()  # re-seeded from the live context
    assert json.loads(state_path.read_text())["library"]["num_slots"] == 4


def test_cli_stdout_is_parseable_json_with_logs_on_stderr(tmp_path) -> None:
    """stdout is the CLI's data channel; log lines there break `... | jq`.

    Campaign regression: `openblade format confirm ... | jq` died with
    "Extra data" because two `tape operation ...` lines preceded the JSON.

    Run as a subprocess deliberately. structlog's factory binds a ``sys.stderr``
    *object* at import time, and under pytest that object is the framework's own
    global-capture stream -- so neither capsys nor capfd observes where the bytes
    really land. Only a real process does.
    """
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    console_script = Path(sys.executable).with_name("openblade")
    if not console_script.exists():
        found = shutil.which("openblade")
        if found is None:
            pytest.skip("openblade console script not installed in this environment")
        console_script = Path(found)

    env = {
        **os.environ,
        "OPENBLADE_BACKEND": "mock",
        "OPENBLADE_DB_URL": f"sqlite:///{tmp_path / 'cli.db'}",
        "OPENBLADE_CACHE_DIR": str(tmp_path / "cache"),
        "OPENBLADE_STAGING_DIR": str(tmp_path / "staging"),
        "OPENBLADE_RESTORE_DIR": str(tmp_path / "restore"),
        "HOME": str(tmp_path),
    }
    seed = subprocess.run(
        [str(console_script), "mock", "init", "--cartridges", "2"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert seed.returncode == 0, seed.stderr

    dry_run = subprocess.run(
        [str(console_script), "format", "dry-run", "--barcode", "MCK00001"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    token = json.loads(dry_run.stdout)["token"]

    # `format confirm` goes through the tape orchestrator, which emits
    # "tape operation queued"/"completed" log lines. Those are what used to
    # land on stdout ahead of the JSON.
    confirm = subprocess.run(
        [str(console_script), "format", "confirm", "--barcode", "MCK00001", "--token", token],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert confirm.returncode == 0, confirm.stderr
    payload = json.loads(confirm.stdout)  # the whole of stdout, not a filtered slice
    assert payload["success"] is True
    assert "tape operation" in confirm.stderr, "log lines went somewhere other than stderr"
