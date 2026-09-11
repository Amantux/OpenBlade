from openblade.hardware.discovery import (
    SAMPLE_LSSCSI_FULL,
    SAMPLE_LSSCSI_NO_CHANGER,
    SAMPLE_SG_MAP_FULL,
    find_tape_changers,
    find_tape_drives,
    parse_lsscsi,
    parse_sg_map,
)
from openblade.hardware.ltfs import SAMPLE_LTFS_DEVICE_LIST, parse_ltfs_device_list
from openblade.hardware.mtx import (
    SAMPLE_MTX_BARCODE_MISSING,
    SAMPLE_MTX_CLEANING,
    SAMPLE_MTX_EMPTY,
    SAMPLE_MTX_LOADED,
    SAMPLE_MTX_THREE_DRIVES,
    parse_mtx_status,
)
from openblade.hardware.sg import (
    SAMPLE_SG_INQ,
    SAMPLE_SG_INQ_EXPORT,
    SAMPLE_SG_INQ_MODERN,
    parse_sg_inq,
)


class TestMtxParser:
    def test_parse_empty_library(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_EMPTY)
        assert status.device == "/dev/sg0"
        assert len(status.drives) == 1
        assert status.drives[0].loaded is False
        assert len(status.slots) == 2
        assert all(slot.occupied is False for slot in status.slots)

    def test_parse_loaded_library(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_LOADED)
        assert len(status.slots) == 4
        assert status.slots[1].barcode == "PHO002L8"
        assert status.slots[2].barcode == "PHO003L8"

    def test_parse_cleaning_tape(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_CLEANING)
        assert status.slots[0].barcode == "CLN001L1"
        assert status.slots[0].is_cleaning is True
        assert status.slots[1].is_cleaning is False

    def test_parse_barcode_missing(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_BARCODE_MISSING)
        assert status.slots[0].occupied is True
        assert status.slots[0].barcode is None

    def test_parse_two_drives(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_LOADED)
        assert len(status.drives) == 2
        assert [drive.drive_id for drive in status.drives] == [0, 1]

    def test_parse_three_drive_i3_status(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_THREE_DRIVES)
        assert status.device == "/dev/sg3"
        assert status.drive_count == 3
        assert status.slot_count == 50
        assert [drive.drive_id for drive in status.drives] == [0, 1, 2]
        assert [drive.loaded for drive in status.drives] == [True, False, True]
        assert [drive.barcode for drive in status.drives] == ["VOL001L8", None, "VOL004L8"]
        # Element 2 was loaded from slot 4 — drive index and slot index are unrelated.
        assert status.drives[2].source_slot == 4

    def test_three_drive_status_cleaning_tape_is_flagged(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_THREE_DRIVES)
        cleaning = [slot.slot_id for slot in status.slots if slot.is_cleaning]
        assert cleaning == [3]

    def test_three_drive_status_skips_import_export_elements(self) -> None:
        # KNOWN GAP (docs/runbooks/real-i3-bringup-plan.md Phase 3.4): the slot regex
        # requires "Storage Element <n>:" so the i3's
        # "Storage Element 51 IMPORT/EXPORT:..." rows are dropped. Pinned here so the
        # behaviour is visible; update this test when I/E parsing is implemented.
        status = parse_mtx_status(SAMPLE_MTX_THREE_DRIVES)
        assert [slot.slot_id for slot in status.slots] == [1, 2, 3, 4, 5]

    def test_drive_loaded_from_slot(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_LOADED)
        assert status.drives[0].loaded is True
        assert status.drives[0].source_slot == 1
        assert status.drives[0].barcode == "PHO001L8"


class TestLsscsiParser:
    def test_parse_full_output(self) -> None:
        devices = parse_lsscsi(SAMPLE_LSSCSI_FULL)
        assert len(devices) == 4
        assert devices[1].device_type == "mediumx"
        assert devices[1].sg_device == "/dev/sg0"
        assert devices[2].block_device == "/dev/st0"

    def test_no_changer_present(self) -> None:
        devices = parse_lsscsi(SAMPLE_LSSCSI_NO_CHANGER)
        assert len(devices) == 1
        assert devices[0].device_type == "disk"

    def test_find_tape_changers(self) -> None:
        changers = find_tape_changers(parse_lsscsi(SAMPLE_LSSCSI_FULL))
        assert len(changers) == 1
        assert changers[0].block_device == "/dev/smc0"

    def test_find_tape_drives(self) -> None:
        drives = find_tape_drives(parse_lsscsi(SAMPLE_LSSCSI_FULL))
        assert [drive.block_device for drive in drives] == ["/dev/st0", "/dev/st1"]

    def test_empty_output(self) -> None:
        assert parse_lsscsi("") == []


class TestSgMapParser:
    def test_parse_sg_map(self) -> None:
        mapping = parse_sg_map(SAMPLE_SG_MAP_FULL)
        assert mapping["/dev/sg0"] == "/dev/smc0"
        assert mapping["/dev/sg2"] == "/dev/st1"

    def test_empty_sg_map(self) -> None:
        assert parse_sg_map("") == {}


class TestLTFSParser:
    def test_parse_device_list(self) -> None:
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST)
        assert len(devices) == 2
        assert devices[0].device == "/dev/st0"
        assert devices[1].description == "IBM ULTRIUM-TD8"

    def test_parse_empty_device_list(self) -> None:
        assert parse_ltfs_device_list("") == []


class TestSgInqParser:
    def test_parse_sg_inq(self) -> None:
        inquiry = parse_sg_inq(SAMPLE_SG_INQ)
        assert inquiry.device_type == "tape"
        assert inquiry.vendor == "IBM"
        assert inquiry.product == "ULTRIUM-TD8"
        assert inquiry.revision == "H3S4"
        assert inquiry.serial == "10WT073819"

    def test_parse_modern_sg3_utils_output(self) -> None:
        """sg3_utils >= 1.4x prints 'Peripheral device type:' and fetches VPD 0x80 by default."""
        inquiry = parse_sg_inq(SAMPLE_SG_INQ_MODERN)
        assert inquiry.device_type == "tape"
        assert inquiry.vendor == "IBM"
        assert inquiry.serial == "10WT073820"

    def test_parse_export_form_serial(self) -> None:
        assert parse_sg_inq(SAMPLE_SG_INQ_EXPORT).serial == "10WT073821"

    def test_serial_is_empty_when_not_reported(self) -> None:
        # A device that does not implement the serial-number VPD page must yield ""
        # rather than a guess — correlation treats "" as unusable.
        without_serial = "\n".join(
            line for line in SAMPLE_SG_INQ.splitlines() if "Unit serial number" not in line
        )
        assert parse_sg_inq(without_serial).serial == ""

    def test_serial_padding_is_stripped(self) -> None:
        # Real drives pad the VPD 0x80 field with trailing spaces.
        assert parse_sg_inq("  Unit serial number: 10WT073819     \n").serial == "10WT073819"
