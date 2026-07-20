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


def test_moveclass_real_documented_bit_values() -> None:
    # Values from the Web Services manual (Rev D): unload=8, no-eject=16, closest=32.
    assert (MoveClass.IMPORT, MoveClass.EXPORT, MoveClass.UNLOAD, MoveClass.NO_EJECT,
            MoveClass.CLOSEST_SLOT) == (2, 4, 8, 16, 32)
    assert MoveClass.from_wire(8).is_unload
    assert MoveClass.from_wire(8).to_wire() == 8  # sends 8, not the deprecated 3
    assert MoveClass.from_wire(24) == (MoveClass.UNLOAD | MoveClass.NO_EJECT)  # 8+16
    assert MoveClass.from_wire(0) == MoveClass.NORMAL
    assert MoveClass.NORMAL.to_wire() == 0


def test_moveclass_accepts_deprecated_unload_and_ignores_unknown_bits() -> None:
    # The deprecated single-integer 3 (=unload) is still accepted on input.
    assert MoveClass.from_wire(3).is_unload
    # But its canonical wire form is the bit-field 8, not 3.
    assert MoveClass.from_wire(3).to_wire() == 8
    # An unknown bit (1) that maps to no flag must NOT be treated as unload.
    assert MoveClass.from_wire(1) == MoveClass.NORMAL
    assert not MoveClass.from_wire(1).is_unload
