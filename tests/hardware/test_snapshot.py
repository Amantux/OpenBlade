"""Unit tests for tools/hardware/snapshot.py — simulator backend, no rig."""

from __future__ import annotations

import json
from pathlib import Path

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig
from tools.hardware.snapshot import (
    EXIT_BAD_INPUT,
    EXIT_CHANGED,
    capture_snapshot,
    diff_snapshots,
    main,
)


def _snap(slots, drives, changer="idle"):
    return {"library_id": "L", "slots": slots, "drives": drives, "changer_state": changer}


def test_capture_against_simulator_is_json_serializable(tmp_path: Path) -> None:
    library = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 's.db'}")).library
    snap = capture_snapshot(library)
    assert snap["slots"] and "slot_id" in snap["slots"][0]
    assert json.loads(json.dumps(snap)) == snap


def test_diff_identical_snapshots_reports_unchanged() -> None:
    s = _snap([{"slot_id": 1, "barcode": "A"}], [{"drive_id": 0, "barcode": None}])
    assert diff_snapshots(s, s)["changed"] is False


def test_diff_reports_moved_cartridge_per_element() -> None:
    before = _snap([{"slot_id": 1, "barcode": "A"}], [{"drive_id": 0, "barcode": None}])
    after = _snap([{"slot_id": 1, "barcode": None}], [{"drive_id": 0, "barcode": "A"}])
    result = diff_snapshots(before, after)
    assert result["changed"] is True
    assert result["slots"][0]["slot_id"] == "1" and result["slots"][0]["after"]["barcode"] is None
    assert result["drives"][0]["after"]["barcode"] == "A"


def test_diff_cli_writes_file_and_rejects_bad_input(tmp_path: Path) -> None:
    good = tmp_path / "a.json"
    good.write_text(json.dumps(_snap([], [])))
    out = tmp_path / "d.json"
    assert main(["diff", str(good), str(good), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["changed"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert main(["diff", str(good), str(bad), "--out", str(out)]) == EXIT_BAD_INPUT


def test_diff_cli_changed_snapshots_exit_1_after_writing(tmp_path: Path) -> None:
    a, b, out = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "d.json"
    a.write_text(json.dumps(_snap([{"slot_id": 1, "barcode": "A"}], [])))
    b.write_text(json.dumps(_snap([{"slot_id": 1, "barcode": None}], [])))
    assert main(["diff", str(a), str(b), "--out", str(out)]) == EXIT_CHANGED
    assert json.loads(out.read_text())["changed"] is True
