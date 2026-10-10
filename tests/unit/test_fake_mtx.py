"""The stateful mtx fake must speak the format ``parse_mtx_status`` consumes."""

from __future__ import annotations

from openblade.hardware.mtx import parse_mtx_status
from tests.fakes.mtx import StatefulMtxRunner

DEV = "/dev/sg3"


def _runner() -> StatefulMtxRunner:
    return StatefulMtxRunner(DEV, barcodes={1: "VOL001L8", 2: "VOL002L8", 52: "VOL052L8"})


def _status(runner: StatefulMtxRunner) -> str:
    result = runner.run(["mtx", "-f", DEV, "status"])
    assert result.returncode == 0
    return result.stdout


def test_fake_mtx_status_round_trips_through_parser() -> None:
    status = parse_mtx_status(_status(_runner()))

    assert (status.drive_count, status.slot_count) == (3, 50)
    assert len(status.slots) == 50
    assert {s.slot_id: s.barcode for s in status.slots if s.occupied} == {
        1: "VOL001L8",
        2: "VOL002L8",
    }
    assert [(s.slot_id, s.barcode) for s in status.import_export_slots] == [
        (51, None),
        (52, "VOL052L8"),
    ]


def test_fake_mtx_load_reports_drive_barcode_and_source_slot() -> None:
    runner = _runner()

    assert runner.run(["mtx", "-f", DEV, "load", "2", "1"]).returncode == 0

    drive = next(d for d in parse_mtx_status(_status(runner)).drives if d.drive_id == 1)
    assert (drive.loaded, drive.barcode, drive.source_slot) == (True, "VOL002L8", 2)


def test_fake_mtx_unload_and_transfer_move_cartridges() -> None:
    runner = _runner()
    runner.run(["mtx", "-f", DEV, "load", "1", "0"])

    assert runner.run(["mtx", "-f", DEV, "unload", "5", "0"]).returncode == 0
    assert runner.run(["mtx", "-f", DEV, "transfer", "5", "6"]).returncode == 0

    full = {s.slot_id: s.barcode for s in parse_mtx_status(_status(runner)).slots if s.occupied}
    assert full == {2: "VOL002L8", 6: "VOL001L8"}


def test_fake_mtx_load_from_empty_slot_fails_with_request_sense() -> None:
    result = _runner().run(["mtx", "-f", DEV, "load", "3", "0"])

    assert result.returncode == 1
    assert result.stderr.startswith("mtx: Request Sense:")


def test_fake_mtx_load_into_full_drive_fails_without_moving_tape() -> None:
    runner = _runner()
    runner.run(["mtx", "-f", DEV, "load", "1", "0"])

    result = runner.run(["mtx", "-f", DEV, "load", "2", "0"])

    assert result.returncode == 1
    assert runner.slots[2] == "VOL002L8"


def test_fake_mtx_unload_empty_drive_or_into_full_slot_fails() -> None:
    runner = _runner()
    assert runner.run(["mtx", "-f", DEV, "unload", "5", "0"]).returncode == 1
    runner.run(["mtx", "-f", DEV, "load", "1", "0"])

    assert runner.run(["mtx", "-f", DEV, "unload", "2", "0"]).returncode == 1


def test_fake_mtx_unknown_argv_returns_2() -> None:
    runner = _runner()

    for argv in (
        ["mtx", "-f", DEV, "inquiry"],
        ["mtx", "-f", "/dev/other", "status"],
        ["mtx", "-f", DEV, "load", "x", "0"],
        ["sg_inq", DEV],
    ):
        assert runner.run(argv).returncode == 2, argv
