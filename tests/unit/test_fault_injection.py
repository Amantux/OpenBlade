from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.simulator.fault_injection import (
    FaultInjector,
    FaultSpec,
    FaultType,
    SimulatorFaultError,
)
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


def _confirmation(barcode: str) -> FormatConfirmation:
    return FormatConfirmation(barcode, SafetyToken.generate("format", barcode))


def _loaded_formatted_backend(
    injector: FaultInjector | None = None,
) -> tuple[MockLibraryBackend, MockLTFSBackend, str]:
    library = MockLibraryBackend(num_slots=4, num_drives=1, fault_injector=injector)
    library.add_cartridge(1, "PHO001L8")
    barcode = "PHO001L8"
    library.load(1, 0)
    ltfs = MockLTFSBackend(library, capacity_bytes=64, fault_injector=injector)
    ltfs.format(barcode, _confirmation(barcode))
    return library, ltfs, barcode


def test_fault_injector_no_faults_initially() -> None:
    injector = FaultInjector()

    assert injector.active_faults() == []
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False


def test_inject_fault_is_active() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))

    assert injector.active_faults() == [FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8")]


def test_should_fault_returns_true_when_matching() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is True


def test_should_fault_returns_false_for_wrong_type() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))

    assert injector.should_fault(FaultType.WRITE_ERROR, "PHO001L8") is False


def test_should_fault_returns_false_for_wrong_target() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO002L8") is False


def test_fault_exhausted_after_max_triggers() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8", max_triggers=1))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is True
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False


def test_context_manager_clears_faults_on_exit() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))

    with injector:
        assert injector.active_faults()

    assert injector.active_faults() == []


def test_trigger_after_delays_fault() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8", trigger_after=2))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is True
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False


def test_multiple_faults_independent() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8", max_triggers=2))
    injector.inject(FaultSpec(FaultType.WRITE_ERROR, target="PHO002L8"))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is True
    assert injector.should_fault(FaultType.WRITE_ERROR, "PHO002L8") is True
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is True
    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8") is False


def test_wildcard_target_matches_any() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="*"))

    assert injector.should_fault(FaultType.LOAD_FAILURE, "PHO999L8") is True


def test_get_error_message_custom() -> None:
    injector = FaultInjector()
    injector.inject(
        FaultSpec(
            FaultType.LOAD_FAILURE,
            target="PHO001L8",
            error_message="custom load failure",
        )
    )
    injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8")

    assert injector.get_error_message(FaultType.LOAD_FAILURE, "PHO001L8") == "custom load failure"


def test_get_error_message_default_fallback() -> None:
    injector = FaultInjector()
    injector.inject(FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8"))
    injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8")

    assert injector.get_error_message(FaultType.LOAD_FAILURE, "PHO001L8") == (
        "Injected load failure for PHO001L8"
    )


def test_active_faults_returns_non_exhausted() -> None:
    injector = FaultInjector()
    exhausted = FaultSpec(FaultType.LOAD_FAILURE, target="PHO001L8")
    remaining = FaultSpec(FaultType.WRITE_ERROR, target="PHO002L8", max_triggers=2)
    injector.inject(exhausted)
    injector.inject(remaining)
    injector.should_fault(FaultType.LOAD_FAILURE, "PHO001L8")

    assert injector.active_faults() == [remaining]


def test_simulator_ltfs_raises_on_write_fault() -> None:
    injector = FaultInjector()
    injector.inject(
        FaultSpec(
            FaultType.WRITE_ERROR,
            target="PHO001L8",
            error_message="write failed on purpose",
        )
    )
    _, ltfs, barcode = _loaded_formatted_backend(injector)
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)

    with pytest.raises(SimulatorFaultError, match="write failed on purpose"):
        ltfs.write_bytes(handle, PurePosixPath("/payload.bin"), b"payload")


def test_simulator_library_raises_on_load_fault() -> None:
    injector = FaultInjector()
    injector.inject(
        FaultSpec(
            FaultType.LOAD_FAILURE,
            target="PHO001L8",
            error_message="load failed on purpose",
        )
    )
    library = MockLibraryBackend(num_slots=4, num_drives=1, fault_injector=injector)
    library.add_cartridge(1, "PHO001L8")

    with pytest.raises(SimulatorFaultError, match="load failed on purpose"):
        library.load(1, 0)
