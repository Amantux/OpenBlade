from openblade.hardware.discovery import (
    SAMPLE_LSSCSI_FULL,
    SAMPLE_LSSCSI_NO_CHANGER,
    SAMPLE_SG_MAP_FULL,
    find_tape_changers,
    find_tape_drives,
    parse_lsscsi,
    parse_sg_map,
    resolve_sg_device,
)
from openblade.hardware.ltfs import (
    SAMPLE_LTFS_DEVICE_LIST,
    SAMPLE_LTFS_DEVICE_LIST_REAL,
    parse_ltfs_device_list,
)
from openblade.hardware.mtx import (
    SAMPLE_MTX_BARCODE_MISSING,
    SAMPLE_MTX_CLEANING,
    SAMPLE_MTX_EMPTY,
    SAMPLE_MTX_HIGH_ADDRESSES,
    SAMPLE_MTX_IRREGULAR,
    SAMPLE_MTX_LOADED,
    SAMPLE_MTX_REAL_SCALAR,
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

    def test_three_drive_status_separates_import_export_elements(self) -> None:
        # Formerly a KNOWN-GAP characterization test: I/E rows used to be
        # dropped entirely. The mhvtl rehearsal fixed the parser to keep them
        # in a SEPARATE list — never in `slots`, which consumers treat as
        # "places a tape may be unloaded to" (folding them in would eject
        # cartridges to the operator mailslot on a full library).
        status = parse_mtx_status(SAMPLE_MTX_THREE_DRIVES)
        assert [slot.slot_id for slot in status.slots] == [1, 2, 3, 4, 5]
        assert [s.slot_id for s in status.import_export_slots] == [51, 52]
        assert status.import_export_slots[1].barcode == "VOL052L8"

    def test_drive_loaded_from_slot(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_LOADED)
        assert status.drives[0].loaded is True
        assert status.drives[0].source_slot == 1
        assert status.drives[0].barcode == "PHO001L8"


class TestMtxParserHighElementAddresses:
    """Real libraries may report high element start addresses (storage 4096+,
    I/E 768+). Ids must round-trip verbatim — parser, lookup, and the mtx
    command line may never renumber, offset, or range-check them."""

    def test_high_slot_ids_parse_verbatim(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_HIGH_ADDRESSES)
        assert [s.slot_id for s in status.slots] == [4096, 4097, 4098, 4099]
        assert [s.slot_id for s in status.import_export_slots] == [768, 769]
        assert status.drives[0].source_slot == 4097
        assert status.slots[0].barcode == "HIA000L8"

    def test_high_slot_id_reaches_mtx_command_verbatim(self) -> None:
        from openblade.domain.policies import RealHardwareGuard
        from openblade.hardware.mtx import MtxChangerBackend
        from openblade.hardware.runner import SafeRunner

        guard = RealHardwareGuard(
            config_backend="real",
            config_real_hardware_enabled=True,
            operator_acknowledgment="high-address round-trip test",
        )
        backend = MtxChangerBackend(
            device="/dev/sg2", runner=SafeRunner(dry_run=True), guard=guard
        )
        result = backend.load(slot=4098, drive=2)
        assert result.details["args"][-2:] == ["4098", "2"]
        result = backend.unload(drive=2, slot=4099)
        assert "4099" in result.details["args"]


class TestMtxParserIrregularForms:
    """Forms a real i3 can emit that mhvtl never does: unknown source
    elements and unlabeled (barcode-less) media. Degrade, never drop."""

    def test_unknown_source_drive_keeps_load_state_and_barcode(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_IRREGULAR)
        assert status.drives[0].loaded is True
        assert status.drives[0].source_slot is None
        assert status.drives[0].barcode == "OB0001L8"

    def test_unknown_source_unlabeled_drive(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_IRREGULAR)
        assert status.drives[1].loaded is True
        assert status.drives[1].source_slot is None
        assert status.drives[1].barcode is None

    def test_unlabeled_slots_are_occupied_with_no_barcode(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_IRREGULAR)
        assert status.slots[0].occupied is True
        assert status.slots[0].barcode is None
        assert status.import_export_slots[0].occupied is True
        assert status.import_export_slots[0].barcode is None


class TestMtxParserAgainstRealOutput:
    """Regressions pinned to byte-accurate `mtx status` output from real hardware.

    Every assertion here failed before the mhvtl rehearsal (Phase 2 of
    docs/runbooks/real-i3-bringup-plan.md) exercised the parser for the first
    time against an actual changer.
    """

    def test_drive_barcode_uses_spaced_volume_tag(self) -> None:
        # mtx writes "VolumeTag = X" for a Data Transfer Element but
        # "VolumeTag=X" for a Storage Element. Requiring the tight form made
        # every tape loaded in a drive parse as barcode=None, which in turn
        # made find_drive_by_barcode() blind and archive jobs fail with
        # "Barcode ... not found in inventory" while the tape sat in drive 0.
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert status.drives[0].loaded is True
        assert status.drives[0].barcode == "OB0007L8"
        assert status.drives[0].source_slot == 6

    def test_storage_barcode_still_uses_tight_volume_tag(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert status.slots[0].barcode == "OB0001L8"

    def test_import_export_slots_are_not_dropped(self) -> None:
        # "Storage Element 9 IMPORT/EXPORT:Empty" does not match "\\d+:", so
        # all four I/E elements used to vanish entirely - a tape in the
        # mailslot was invisible to the parser.
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert [slot.slot_id for slot in status.import_export_slots] == [9, 10, 11, 12]

    def test_import_export_slots_are_kept_out_of_storage_slots(self) -> None:
        # They must NOT land in `slots`. Consumers treat that list as "places a
        # tape may be parked or unloaded to" - notably _first_empty_slot() in
        # the AML moveMedium route. On a full library the first EMPTY element
        # is the mailslot, so folding them in would unload cartridges to the
        # operator front panel.
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert [slot.slot_id for slot in status.slots] == list(range(1, 9))
        assert all(not slot.is_import_export for slot in status.slots)
        assert all(slot.is_import_export for slot in status.import_export_slots)

    def test_slot_count_covers_both_kinds(self) -> None:
        # mtx's header counts storage AND import/export elements, so the
        # header figure must equal the two lists together.
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert status.slot_count == 12
        assert len(status.slots) + len(status.import_export_slots) == status.slot_count

    def test_all_slots_merges_in_element_order(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert [slot.slot_id for slot in status.all_slots] == list(range(1, 13))

    def test_barcode_in_import_export_slot_is_visible(self) -> None:
        # A tape parked in the mailslot must still be readable somewhere,
        # otherwise the operator sees media the software swears is absent.
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        slot = next(slot for slot in status.import_export_slots if slot.slot_id == 11)
        assert slot.occupied is True
        assert slot.barcode == "OB0009L8"

    def test_empty_drives_have_no_barcode(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        assert [drive.drive_id for drive in status.drives] == [0, 1, 2]
        assert status.drives[1].loaded is False
        assert status.drives[1].barcode is None

    def test_cleaning_cartridge_detected_in_real_output(self) -> None:
        status = parse_mtx_status(SAMPLE_MTX_REAL_SCALAR)
        slot = next(slot for slot in status.slots if slot.slot_id == 5)
        assert slot.is_cleaning is True


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


class TestLTFSParserAgainstRealOutput:
    """Regressions pinned to real `ltfs -o device_list` output (LTFS 2.4.8.4)."""

    def test_parses_real_device_list(self) -> None:
        # The hand-written SAMPLE_LTFS_DEVICE_LIST above is fictional; no
        # shipping LTFS emits "LTFS14001I <n>: <dev>". Against real output the
        # old regex matched nothing at all.
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST_REAL)
        assert [device.device for device in devices] == ["/dev/sg4", "/dev/sg2", "/dev/sg1"]

    def test_real_devices_are_indexed_positionally(self) -> None:
        # Real LTFS does not number its devices, so index comes from order.
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST_REAL)
        assert [device.index for device in devices] == [0, 1, 2]

    def test_serial_numbers_are_captured(self) -> None:
        # Serial is how drives get correlated to changer elements; ordering is
        # not trustworthy (see docs/runbooks/mhvtl-rehearsal.md).
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST_REAL)
        assert [device.serial for device in devices] == [
            "OBLADE_D03",
            "OBLADE_D02",
            "OBLADE_D01",
        ]

    def test_padding_and_trailing_period_are_stripped(self) -> None:
        # LTFS pads vendor/product to the SCSI INQUIRY widths and ends the line
        # with '.', both of which would otherwise land in the parsed values.
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST_REAL)
        assert devices[0].description == "IBM ULT3580-TD8"

    def test_legacy_format_still_parses(self) -> None:
        devices = parse_ltfs_device_list(SAMPLE_LTFS_DEVICE_LIST)
        assert [device.device for device in devices] == ["/dev/st0", "/dev/st1"]
        assert devices[0].serial is None


class TestResolveSgDevice:
    """`resolve_sg_device` maps tape/changer nodes onto their sg node.

    SCSI pass-through tools need the sg node: LTFS's sg backend misreads
    /dev/stN, and sg_inq against the REWINDING /dev/stN exits non-zero on an
    empty drive. st and sg numbers are allocated independently, so the mapping
    must be read from sysfs rather than derived from the number.
    """

    @staticmethod
    def _sysfs(tmp_path, class_name: str, node: str, sg: str):
        generic = tmp_path / "class" / class_name / node / "device" / "scsi_generic" / sg
        generic.mkdir(parents=True)
        return tmp_path

    def test_maps_rewinding_tape_node_to_sg(self, tmp_path) -> None:
        root = self._sysfs(tmp_path, "scsi_tape", "st0", "sg1")
        assert resolve_sg_device("/dev/st0", sysfs_root=root) == "/dev/sg1"

    def test_maps_no_rewind_tape_node_to_sg(self, tmp_path) -> None:
        root = self._sysfs(tmp_path, "scsi_tape", "nst0", "sg1")
        assert resolve_sg_device("/dev/nst0", sysfs_root=root) == "/dev/sg1"

    def test_number_is_not_assumed_to_match(self, tmp_path) -> None:
        # st2 -> sg4 is a real pairing from the rehearsal rig. Anything that
        # derives "sg2" from "st2" is wrong.
        root = self._sysfs(tmp_path, "scsi_tape", "st2", "sg4")
        assert resolve_sg_device("/dev/st2", sysfs_root=root) == "/dev/sg4"

    def test_maps_changer_node_to_sg(self, tmp_path) -> None:
        root = self._sysfs(tmp_path, "scsi_changer", "sch0", "sg3")
        assert resolve_sg_device("/dev/sch0", sysfs_root=root) == "/dev/sg3"

    def test_sg_device_passes_through(self, tmp_path) -> None:
        assert resolve_sg_device("/dev/sg1", sysfs_root=tmp_path) == "/dev/sg1"

    def test_unmapped_device_passes_through(self, tmp_path) -> None:
        # Callers apply this unconditionally, so an unknown node must not raise
        # or return something bogus.
        assert resolve_sg_device("/dev/st9", sysfs_root=tmp_path) == "/dev/st9"

    def test_non_dev_path_passes_through(self, tmp_path) -> None:
        assert resolve_sg_device("not-a-device", sysfs_root=tmp_path) == "not-a-device"


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
