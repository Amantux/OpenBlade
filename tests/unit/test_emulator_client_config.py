from __future__ import annotations

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig, load_config


def test_load_config_parses_emulator_urls(monkeypatch) -> None:
    monkeypatch.setenv(
        "OPENBLADE_EMULATOR_URLS",
        " http://emulator-1:8010/ ,http://emulator-2:8010 ",
    )
    config = load_config()
    assert config.emulator_urls == (
        "http://emulator-1:8010",
        "http://emulator-2:8010",
    )


def test_load_config_parses_emulator_latency_controls(monkeypatch) -> None:
    monkeypatch.setenv("EMULATOR_LATENCY_PROFILE", "hardware")
    monkeypatch.setenv("OPENBLADE_EMULATOR_LATENCY_ENABLED", "false")
    config = load_config()
    assert config.emulator_latency_profile == "hardware"
    assert config.emulator_latency_enabled is False


def test_create_context_seeds_libraries_from_configured_emulator_urls(tmp_path) -> None:
    config = OpenBladeConfig(
        db_url=f"sqlite:///{tmp_path / 'configured-emulators.db'}",
        emulator_urls=(
            "http://emulator-1:8010",
            "http://emulator-2:8010",
            "http://emulator-3:8010",
            "http://emulator-4:8010",
        ),
    )

    context = create_context(config)
    libraries = context.catalog.list_library_instances()

    assert [library.emulator_url for library in libraries] == [
        "http://emulator-1:8010",
        "http://emulator-2:8010",
        "http://emulator-3:8010",
        "http://emulator-4:8010",
    ]
    assert [library.name for library in libraries] == [
        "Primary Tape Library",
        "Secondary Archive",
        "Cold Storage Vault",
        "Tape Library 4",
    ]
    assert [library.serial_number for library in libraries] == [
        "OB-SCALAR-I3-001",
        "OB-SCALAR-I3-002",
        "OB-SCALAR-I3-003",
        "OB-SCALAR-I3-004",
    ]
