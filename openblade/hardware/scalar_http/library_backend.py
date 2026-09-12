"""``LibraryBackend`` implemented over the Quantum AML Web Services API.

Scope: the read/control-plane surface (inventory, drive/slot lookup) mapped from
``GET /aml/physicalLibrary/elements``, robotic moves over
``POST /aml/media/operations/moveMedium``, and ``drive_device`` — the host
device correlation the LTFS data path needs.

What the Web Services contract actually gives us for ``drive_device``
----------------------------------------------------------------------
``drive_device(drive_id)`` has to turn a *library drive element address* into a
host tape device (``/dev/nst*``). Doing that from the library alone needs the
library to say "element N holds the drive with serial S". **It does not.**
Verified against both halves of the contract:

* ``GET /aml/drives`` returns drive objects that **do** carry ``serialNumber``
  (``openblade/api/routes_aml_drives.py``, model ``Drive``), and every
  drive-scoped path in the manual matrix is keyed by it —
  ``/aml/drive/{serialNumber}``, ``/aml/drive/{serialNumber}/dataPath``, … (rows
  106 and 124-148 of ``openblade/emulator_contract/quantum_i3_endpoint_catalog.md``).
  But a drive object carries **no element address**; its ``location`` is a human
  bay label, not the element coordinate used by ``moveMedium``.
* ``GET /aml/physicalLibrary/elements`` returns drive elements carrying only
  ``address``, ``state`` and ``barcode`` — **no serial**
  (``ElementResource`` in ``openblade/api/routes_aml_library.py``).

So the wire contract exposes serials *and* element addresses, but never the join
between them. The endpoint catalog is an endpoint inventory only — it documents
no response fields at all — and the Rev H matrix records per-endpoint
requirements, not schemas, so nothing there supplies the join either. Joining
the two lists by *position* (drive list order ↔ element order) is precisely the
positional guess ``openblade/hardware/correlation.py`` exists to refuse; it would
write LTFS into the wrong drive on any library whose two lists disagree.

**The limitation, stated honestly:** against a real i3 this backend cannot derive
the element↔serial mapping by itself. It therefore requires the same
operator-declared ``OPENBLADE_DRIVE_SERIAL_MAP`` the SCSI backend uses, and
refuses (typed :class:`DriveCorrelationError`) rather than guessing when it is
absent.

⚠️ **The element ids mean something different here than on the SCSI backend.**
``correlation.py`` documents the map's element index as mtx's 0-based Data
Transfer Element numbering, because that is what ``mtx`` reports. This backend
never sees an mtx DTE: the id it is asked about is the library's own element
``address`` from ``GET /aml/physicalLibrary/elements`` (see :meth:`inventory`),
and the i3 numbers drive bays from 1. A map written in the wrong number space —
even one merely shifted by one — passes the serial checks and then returns a
neighbouring drive's device, which is the wrong-drive LTFS write this whole
module exists to prevent. So the declared element ids are required to BE this
library's element addresses, checked for exact set equality against a live
``inventory()`` before the correlation is used, and refused otherwise.

On top of the SCSI backend's live ``sg_inq`` check, it adds one check
``mtx`` cannot make: the declared serials are cross-checked against the serials
this library reports on ``GET /aml/drives`` (see
``correlation.verify_against_library_serials``), which catches a map that
belongs to a *different* library — but only when the two sides spell serials
comparably enough to overlap at all. A fully disjoint result is warned about,
not refused, because nothing here can tell it apart from a formatting
difference. Lifting the limitation needs the library to
publish the join — READ ELEMENT STATUS with DVCID, or a documented AML field
carrying the element address on the drive object.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from openblade.domain.errors import DriveCorrelationError, OpenBladeError
from openblade.domain.models import (
    Barcode,
    CartridgeState,
    ChangerState,
    DriveState,
    DriveStatus,
    LibraryInventory,
    MountState,
    OperationResult,
    SlotState,
)
from openblade.domain.scalar_coordinate import MoveClass
from openblade.hardware.correlation import (
    SOURCE_SERIAL_MAP,
    DriveCorrelation,
    verify_against_library_serials,
)
from openblade.hardware.scalar_http.errors import ScalarHttpError
from openblade.hardware.scalar_http.session import ScalarHttpSession

logger = logging.getLogger(__name__)

_DRIVE_STATES = {state.value: state for state in DriveState}

#: Builds the host-device correlation on first use. Deferred rather than built in
#: ``__init__`` because it probes local hardware (``sg_inq``): a deployment that
#: uses this backend for robotics only must not be forced to have tape devices.
DriveCorrelationFactory = Callable[[], DriveCorrelation]

_NO_CORRELATION = (
    "This library is driven over AML Web Services, which does not publish which "
    "drive serial sits in which drive element, so the host device for a drive "
    "cannot be derived from the library. Declare it explicitly: set "
    "OPENBLADE_DRIVE_DEVICES to the host tape devices and OPENBLADE_DRIVE_SERIAL_MAP "
    "to '<serial>:<element>,...', where <serial> is the drive's SCSI unit serial "
    "number (`sg_inq /dev/sgN`) and <element> is that drive's element address as "
    "reported by GET /aml/physicalLibrary/elements on this library. "
    "Refusing to guess which device is drive element {drive_id}."
)

_UNVERIFIED_CORRELATION = (
    "Drive element {drive_id} has no verified host device: the correlation is "
    "{source}, not an OPENBLADE_DRIVE_SERIAL_MAP checked against the attached "
    "drives. The Web Services backend will not fall back to positional order — "
    "element order and /dev/nst* order are set by two unrelated systems, so a "
    "guess here writes LTFS into the wrong drive. Set OPENBLADE_DRIVE_SERIAL_MAP."
)


def _drive_state(value: Any) -> DriveState:
    return _DRIVE_STATES.get(str(value), DriveState.EMPTY)


def _barcode_or_none(value: Any) -> Barcode | None:
    text = str(value).strip() if value else ""
    return Barcode(text) if text else None


class ScalarHttpLibraryBackend:
    """Reads a real Scalar i3 inventory over ``/aml`` Web Services.

    Satisfies the read half of ``openblade.domain.backends.LibraryBackend``. State
    is fetched live from the library on each call (no local cache) so the view
    always reflects the physical device.
    """

    def __init__(
        self,
        session: ScalarHttpSession,
        *,
        library_id: str = "scalar-i3",
        elements_path: str = "/aml/physicalLibrary/elements",
        move_medium_path: str = "/aml/media/operations/moveMedium",
        drives_path: str = "/aml/drives",
        correlation_factory: DriveCorrelationFactory | None = None,
    ) -> None:
        self._session = session
        self._library_id = library_id
        self._elements_path = elements_path
        self._move_medium_path = move_medium_path
        self._drives_path = drives_path
        self._correlation_factory = correlation_factory
        self._correlation: DriveCorrelation | None = None
        self._mount_states: dict[int, MountState] = {}

    @property
    def library_id(self) -> str:
        return self._library_id

    def _elements(self) -> list[dict[str, Any]]:
        body = self._session.get_json(self._elements_path)
        element_list = body.get("elementList")
        elements = element_list.get("element") if isinstance(element_list, dict) else None
        return [e for e in elements if isinstance(e, dict)] if isinstance(elements, list) else []

    def inventory(self) -> LibraryInventory:
        slots: list[SlotState] = []
        drives: list[DriveStatus] = []
        for element in self._elements():
            element_type = element.get("type")
            address = element.get("address")
            if address is None:
                continue
            if element_type == "slot":
                barcode = _barcode_or_none(element.get("barcode"))
                slots.append(
                    SlotState(slot_id=int(address), barcode=barcode, occupied=barcode is not None)
                )
            elif element_type == "drive":
                drives.append(
                    DriveStatus(
                        drive_id=int(address),
                        barcode=_barcode_or_none(element.get("barcode")),
                        drive_state=_drive_state(element.get("state")),
                        mount_state=MountState.UNMOUNTED,
                    )
                )
        return LibraryInventory(
            library_id=self._library_id,
            slots=slots,
            drives=drives,
            changer_state=ChangerState.IDLE,
        )

    def get_drive(self, drive_id: int) -> DriveStatus:
        for drive in self.inventory().drives:
            if drive.drive_id == drive_id:
                return drive
        raise OpenBladeError(f"Drive {drive_id} not found on the library")

    def get_slot(self, slot_id: int) -> SlotState:
        for slot in self.inventory().slots:
            if slot.slot_id == slot_id:
                return slot
        raise OpenBladeError(f"Slot {slot_id} not found on the library")

    def find_slot_by_barcode(self, barcode: str) -> int | None:
        target = Barcode(barcode).value
        for slot in self.inventory().slots:
            if slot.barcode is not None and slot.barcode.value == target:
                return slot.slot_id
        return None

    def find_drive_by_barcode(self, barcode: str) -> int | None:
        target = Barcode(barcode).value
        for drive in self.inventory().drives:
            if drive.barcode is not None and drive.barcode.value == target:
                return drive.drive_id
        return None

    def get_all_barcodes(self) -> list[str]:
        inventory = self.inventory()
        barcodes = [slot.barcode.value for slot in inventory.slots if slot.barcode is not None]
        barcodes.extend(
            drive.barcode.value for drive in inventory.drives if drive.barcode is not None
        )
        return barcodes

    # -- robotics (write path) ------------------------------------------------

    @staticmethod
    def _coordinate(element_type: str, address: int) -> dict[str, Any]:
        return {"elementAddress": int(address), "elementType": element_type}

    def _move_medium(
        self,
        source: dict[str, Any],
        destination: dict[str, Any] | None = None,
        *,
        move_class: int = 0,
    ) -> OperationResult:
        body: dict[str, Any] = {"sourceCoordinate": source, "moveClass": move_class}
        if destination is not None:
            body["destinationCoordinate"] = destination
        try:
            result = self._session.post_json(self._move_medium_path, json={"moveMedium": body})
        except ScalarHttpError as error:
            details = {"customCode": error.custom_code} if error.custom_code is not None else {}
            return OperationResult(success=False, message=str(error), details=details)
        message = str(result.get("description") or result.get("summary") or "moveMedium completed")
        return OperationResult(success=True, message=message)

    def load(self, source_slot: int, drive_id: int) -> OperationResult:
        return self._move_medium(
            self._coordinate("slot", source_slot), self._coordinate("drive", drive_id)
        )

    def unload(self, drive_id: int, target_slot: int) -> OperationResult:
        # Real i3 unload uses moveClass=8 (bit field) with the drive source; the
        # target slot is sent as a hint (Web Services manual). See
        # docs/reference/i3-contract-notes.md.
        return self._move_medium(
            self._coordinate("drive", drive_id),
            self._coordinate("slot", target_slot),
            move_class=MoveClass.UNLOAD.to_wire(),
        )

    def move(self, source_slot: int, target_slot: int) -> OperationResult:
        return self._move_medium(
            self._coordinate("slot", source_slot), self._coordinate("slot", target_slot)
        )

    # -- state helpers used by the LTFS data path -----------------------------

    def get_cartridge_state(self, barcode: str) -> CartridgeState:
        target = Barcode(barcode).value
        inventory = self.inventory()
        if any(d.barcode is not None and d.barcode.value == target for d in inventory.drives):
            return CartridgeState.IN_DRIVE
        if any(s.barcode is not None and s.barcode.value == target for s in inventory.slots):
            return CartridgeState.IN_SLOT
        return CartridgeState.MISSING

    def set_drive_mount_state(self, drive_id: int, state: MountState) -> None:
        # LTFS mount state is host-side (the Web Services API has no concept of it).
        # Track it in-memory so the LTFS backend can query/round-trip it.
        self._mount_states[drive_id] = state

    # -- host device correlation ----------------------------------------------

    def library_drive_serials(self) -> list[str] | None:
        """Serial numbers this library reports on ``GET /aml/drives``.

        ``None`` means the list could not be read — the endpoint is absent, the
        request failed, or the payload had an unexpected shape. That is absence of
        evidence, not evidence of a mismatch, so it is reported as ``None`` and
        never conflated with "the library reports no drives" (``[]``).
        """
        try:
            body = self._session.get_json(self._drives_path)
        except (ScalarHttpError, httpx.HTTPError) as error:
            # httpx.HTTPError covers connect/timeout/TLS failures, which
            # ScalarHttpSession does not wrap (it only converts >=400 responses).
            # Letting one escape would both break this method's "None means it
            # could not be read" contract and put raw upstream text — which for
            # OPENBLADE_SCALAR_URL can embed credentials — into a traceback.
            logger.warning(
                "could not read %s for the drive-serial cross-check: %s",
                self._drives_path,
                type(error).__name__,
            )
            return None
        drive_list = body.get("driveList")
        drives = drive_list.get("drive") if isinstance(drive_list, dict) else None
        if not isinstance(drives, list):
            return None
        return [
            str(drive["serialNumber"])
            for drive in drives
            if isinstance(drive, dict) and drive.get("serialNumber")
        ]

    def _resolve_correlation(self) -> DriveCorrelation:
        """Build, check and cache the element -> host-device correlation.

        Cached after the first successful resolution: the serial cross-check costs
        an ``sg_inq`` per drive plus one library round trip, and it is asked for on
        every mount. A refusal is NOT cached — it is re-raised from a fresh attempt
        so fixing the configuration and retrying works without a restart.
        """
        if self._correlation is not None:
            return self._correlation

        if self._correlation_factory is None:
            raise DriveCorrelationError(_NO_CORRELATION.format(drive_id="<unknown>"))

        correlation = self._correlation_factory()
        if correlation.source != SOURCE_SERIAL_MAP:
            raise DriveCorrelationError(
                _UNVERIFIED_CORRELATION.format(drive_id="<unknown>", source=correlation.source)
            )

        self._refuse_on_element_address_mismatch(correlation)

        warnings = verify_against_library_serials(
            correlation=correlation,
            library_serials=self.library_drive_serials(),
            source=f"library {self._library_id}",
        )
        for warning in warnings:
            logger.warning("drive correlation: %s", warning)

        self._correlation = correlation
        return correlation

    def _refuse_on_element_address_mismatch(self, correlation: DriveCorrelation) -> None:
        """Refuse unless the declared element ids ARE this library's element addresses.

        The load-bearing check, and the one the SCSI backend has no need for. The
        id this backend is asked about comes from ``GET /aml/physicalLibrary/
        elements`` → ``address`` (see :meth:`inventory`), while
        ``OPENBLADE_DRIVE_SERIAL_MAP`` is documented in terms of *mtx* Data
        Transfer Element numbering, which is 0-based and produced by a different
        system. Nothing guarantees the two number spaces agree — the i3 web UI
        already numbers drive bays from 1 — and if they are merely shifted, every
        other check here still passes while ``drive_device`` hands back a
        neighbouring drive's device and LTFS is written to the wrong cartridge.

        Equality, not containment, in both directions:

        * a declared element the library does not have means the map is written in
          the wrong number space (or for another library);
        * a library drive element the map does not cover would have a cartridge
          loaded into it by a scheduler sized from the library, and then no device
          to mount it — the cartridge is stranded. ``correlate_drives`` already
          refuses that case for the SCSI backend; this is the same rule.
        """
        declared = {entry.drive_id for entry in correlation.entries}
        reported = {drive.drive_id for drive in self.inventory().drives}
        if declared == reported:
            return
        raise DriveCorrelationError(
            "OPENBLADE_DRIVE_SERIAL_MAP does not describe this library's drive elements. "
            f"Declared element(s): {sorted(declared)}. "
            f"Library {self._library_id} reports drive element address(es): {sorted(reported)}. "
            "The map's element ids must be the library's OWN element addresses, which is "
            "what load/unload and this device lookup use — not the 0-based mtx Data "
            "Transfer Element numbering, and not the 1-based bay numbers on the i3 web "
            "UI. A map that is merely shifted would pass every other check here and then "
            "write LTFS to the wrong drive, so refusing rather than guessing."
        )

    def drive_device(self, drive_id: int) -> str:
        """Host tape device for a library drive element.

        The element -> serial join is operator-declared (see the module docstring
        for why the Web Services contract cannot supply it) and machine-checked
        twice: against the serials read live from the attached drives, and against
        the serials this library reports. Anything short of that refuses with a
        :class:`~openblade.domain.errors.DriveCorrelationError` naming what is
        missing; it never falls back to positional order.
        """
        if self._correlation_factory is None:
            raise DriveCorrelationError(_NO_CORRELATION.format(drive_id=drive_id))
        try:
            correlation = self._resolve_correlation()
        except DriveCorrelationError as error:
            # Re-raise with the element the caller actually asked about; the
            # resolution step does not know it.
            raise DriveCorrelationError(str(error).replace("<unknown>", str(drive_id))) from error
        return correlation.device_for(drive_id)
