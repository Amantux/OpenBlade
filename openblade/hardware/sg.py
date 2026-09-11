from __future__ import annotations

"""sg3_utils wrappers for low-level SCSI tape operations."""

import re
from dataclasses import dataclass

from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import SafeRunner

SAMPLE_SG_INQ = """
standard INQUIRY:
  PQual=0  Device type: tape  RMB=1  LU_CONG=0  version=0x06
  Vendor identification: IBM
  Product identification: ULTRIUM-TD8
  Product revision level: H3S4
  Unit serial number: 10WT073819
"""

# Real output shape of sg3_utils >= 1.4x (verified against sg_inq "2.10 20210328",
# sg3-utils 1.46): the device type moves onto the `length=` line and is spelled
# "Peripheral device type:", and the serial number VPD page (0x80) is fetched by
# DEFAULT — no flag is required (`--only`/`-o` is what SUPPRESSES it).
SAMPLE_SG_INQ_MODERN = """
standard INQUIRY:
  PQual=0  PDT=1  RMB=1  LU_CONG=0  hot_pluggable=0  version=0x06  [SPC-4]
  [AERC=0]  [TrmTsk=0]  NormACA=0  HiSUP=0  Resp_data_format=2
    length=96 (0x60)   Peripheral device type: tape
 Vendor identification: IBM
 Product identification: ULTRIUM-TD8
 Product revision level: H3S4
 Unit serial number: 10WT073820
"""

# `sg_inq --export --page=0x80` (a.k.a. `sg_inq -u -p 0x80`) prints the serial in
# udev key=value form instead. Parsed too so an operator who captured output that
# way still gets a usable serial.
SAMPLE_SG_INQ_EXPORT = """
SCSI_VENDOR=IBM
SCSI_MODEL=ULTRIUM-TD8
SCSI_REVISION=H3S4
SCSI_SERIAL=10WT073821
"""

_DEVICE_TYPE_RE = re.compile(r"(?:Peripheral d|D)evice type:\s+(?P<device_type>[^\s]+)")
_VENDOR_RE = re.compile(r"Vendor identification:\s+(?P<vendor>.+)$")
_PRODUCT_RE = re.compile(r"Product identification:\s+(?P<product>.+)$")
_REVISION_RE = re.compile(r"Product revision level:\s+(?P<revision>.+)$")
_SERIAL_RE = re.compile(r"Unit serial number:\s+(?P<serial>.+)$")
_SERIAL_EXPORT_RE = re.compile(r"^SCSI_SERIAL=(?P<serial>.*)$")


@dataclass(frozen=True)
class ScsiInquiry:
    device_type: str
    vendor: str
    product: str
    revision: str
    # Unit serial number (VPD page 0x80). Empty when the device does not report
    # one — never guess; drive correlation treats "" as unusable.
    serial: str = ""


@dataclass(frozen=True)
class SgDeviceInfo:
    device: str
    inquiry: ScsiInquiry


def parse_sg_inq(output: str) -> ScsiInquiry:
    """Parse `sg_inq` output."""
    device_type = "unknown"
    vendor = ""
    product = ""
    revision = ""
    serial = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        device_type_match = _DEVICE_TYPE_RE.search(line)
        if device_type_match is not None:
            device_type = device_type_match.group("device_type").strip()
            continue
        vendor_match = _VENDOR_RE.search(line)
        if vendor_match is not None:
            vendor = vendor_match.group("vendor").strip()
            continue
        product_match = _PRODUCT_RE.search(line)
        if product_match is not None:
            product = product_match.group("product").strip()
            continue
        revision_match = _REVISION_RE.search(line)
        if revision_match is not None:
            revision = revision_match.group("revision").strip()
            continue
        serial_match = _SERIAL_RE.search(line) or _SERIAL_EXPORT_RE.match(line)
        if serial_match is not None:
            serial = serial_match.group("serial").strip()

    return ScsiInquiry(
        device_type=device_type,
        vendor=vendor,
        product=product,
        revision=revision,
        serial=serial,
    )


def sg_inq(device: str, runner: SafeRunner, guard: RealHardwareGuard) -> ScsiInquiry:
    guard.validate()
    if runner.dry_run:
        return parse_sg_inq(SAMPLE_SG_INQ)
    result = runner.run(["sg_inq", device], timeout=30)
    result.raise_on_error()
    return parse_sg_inq(result.stdout)
