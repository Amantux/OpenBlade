"""Read-only drive health: SCSI inquiry plus TapeAlert flags.

The same report ``openblade hardware drive-health`` prints
(``openblade/cli/fuse_and_health.py``), served over HTTP so the console can show
it. **Read-only and guarded**: every path goes through
:func:`openblade.hardware.safety.require_real_hardware`, so with the simulator
backend -- the default -- this answers 503 with the curated reason and touches no
device. There is no write here and there must never be one: TapeAlert is a LOG
SENSE read, and this module exists so an operator can *see* a failing drive.

A drive that does not implement the TapeAlert log page is reported with
``tapeAlertSupported: false`` and a reason. That is a fact about the drive, not an
error, and it is a 200.
"""

from __future__ import annotations

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from openblade.bootstrap import AppContext, get_context
from openblade.domain.errors import RealHardwareDisabledError
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.discovery import discover_library
from openblade.hardware.runner import SafeRunner
from openblade.hardware.safety import require_real_hardware
from openblade.hardware.sg import ScsiInquiry, sg_inq
from openblade.hardware.tapealert import TapeAlertReport, read_tape_alerts

router = APIRouter(prefix="/hardware", tags=["hardware"])


class DriveInquiryResponse(BaseModel):
    deviceType: str
    vendor: str
    product: str
    revision: str
    #: Empty when the drive reports no unit serial number. Never guessed.
    serial: str


class TapeAlertFlagResponse(BaseModel):
    #: None when ``sg_logs`` named a flag this build's spec table does not know.
    number: int | None
    name: str
    severity: str


class DriveHealthResponse(BaseModel):
    device: str
    inquiry: DriveInquiryResponse
    tapeAlertSupported: bool
    tapeAlertReason: str | None
    #: Severity of the worst flag that is SET, or None when nothing is set.
    worstSeverity: str | None
    flagsRead: int
    activeFlags: list[TapeAlertFlagResponse]


class DriveHealthListResponse(BaseModel):
    drives: list[DriveHealthResponse]


def _serialize(device: str, inquiry: ScsiInquiry, report: TapeAlertReport) -> DriveHealthResponse:
    worst = report.worst_severity
    return DriveHealthResponse(
        device=device,
        inquiry=DriveInquiryResponse(
            deviceType=inquiry.device_type,
            vendor=inquiry.vendor,
            product=inquiry.product,
            revision=inquiry.revision,
            serial=inquiry.serial,
        ),
        tapeAlertSupported=report.supported,
        tapeAlertReason=report.reason,
        worstSeverity=None if worst is None else str(worst),
        flagsRead=len(report.flags),
        activeFlags=[
            TapeAlertFlagResponse(number=flag.number, name=flag.name, severity=str(flag.severity))
            for flag in report.active
        ],
    )


def _collect(
    devices: list[str] | None, runner: SafeRunner, guard: RealHardwareGuard
) -> list[DriveHealthResponse]:
    """Blocking half: discovery plus one INQUIRY and one LOG SENSE per drive."""
    targets = devices
    if targets is None:
        discovery = discover_library(runner, guard)
        targets = [
            node
            for node in (drive.sg_device or drive.block_device for drive in discovery.drives)
            if node is not None
        ]
    return [
        _serialize(target, sg_inq(target, runner, guard), read_tape_alerts(target, runner, guard))
        for target in targets
    ]


@router.get("/drive-health", response_model=DriveHealthListResponse)
async def get_drive_health(
    device: str | None = Query(
        default=None,
        min_length=1,
        max_length=256,
        description="Tape device to inspect, e.g. /dev/nst0. Default: every discovered drive.",
    ),
    context: AppContext = Depends(get_context),
) -> DriveHealthListResponse:
    """Inquiry data and TapeAlert flags for one drive, or every discovered drive."""
    try:
        guard = require_real_hardware(context.config)
    except RealHardwareDisabledError as exc:
        # 503, not 403: nothing is wrong with the request or the caller's rights --
        # this deployment has no real drives to inspect. The detail is the curated
        # guard message naming the two variables that turn it on.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from None

    runner = SafeRunner(dry_run=context.config.hardware_dry_run)
    devices = None if device is None else [device]
    # Off the event loop: `sg_inq`/`sg_logs` are subprocesses with a 30s timeout
    # each, and this process also serves the AML emulator parity surface.
    drives = await to_thread.run_sync(_collect, devices, runner, guard)
    return DriveHealthListResponse(drives=drives)
