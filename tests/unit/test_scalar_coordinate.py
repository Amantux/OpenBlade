"""Tests for the faithful ScalarCoordinate + MoveClass models (roadmap Phase 0)."""

from __future__ import annotations

import pytest

from openblade.domain.scalar_coordinate import MoveClass, ScalarCoordinate


def test_coordinate_preserves_full_shape_round_trip() -> None:
    data = {"frame": 0, "rack": 1, "section": 2, "column": 3, "row": 4, "type": 5}
    coord = ScalarCoordinate.from_dict(data)
    assert coord.section == 2 and coord.column == 3 and coord.element_type == 5
    assert coord.to_dict() == data  # not reduced to an integer


def test_coordinate_accepts_element_type_alias() -> None:
    coord = ScalarCoordinate.from_dict(
        {"frame": 0, "rack": 1, "section": 1, "column": 1, "row": 1, "element_type": 7}
    )
    assert coord.element_type == 7


def test_coordinate_rejects_malformed_dict() -> None:
    with pytest.raises(ValueError):
        ScalarCoordinate.from_dict({"frame": 0, "rack": 1})  # missing fields


def test_moveclass_is_a_composable_bit_field() -> None:
    combo = MoveClass.UNLOAD | MoveClass.NO_EJECT
    assert combo.is_unload
    assert combo & MoveClass.NO_EJECT
    assert not (MoveClass.IMPORT & MoveClass.EXPORT)


def test_moveclass_wire_mapping_round_trips_current_emulator_values() -> None:
    # Emulator-native values (0 normal, 3 unload) — the certification point.
    assert MoveClass.from_wire(0) == MoveClass.NORMAL
    assert MoveClass.from_wire(3).is_unload
    assert MoveClass.from_wire(3).to_wire() == 3
    assert MoveClass.NORMAL.to_wire() == 0


def test_moveclass_unknown_wire_value_falls_back_to_normal() -> None:
    # An uncertified/unknown integer must not be silently treated as unload.
    assert MoveClass.from_wire(8) == MoveClass.NORMAL
    assert not MoveClass.from_wire(8).is_unload
