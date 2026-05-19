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
"""

_DEVICE_TYPE_RE = re.compile(r"Device type:\s+(?P<device_type>[^\s]+)")
_VENDOR_RE = re.compile(r"Vendor identification:\s+(?P<vendor>.+)$")
_PRODUCT_RE = re.compile(r"Product identification:\s+(?P<product>.+)$")
_REVISION_RE = re.compile(r"Product revision level:\s+(?P<revision>.+)$")


@dataclass(frozen=True)
class ScsiInquiry:
    device_type: str
    vendor: str
    product: str
    revision: str


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

    return ScsiInquiry(
        device_type=device_type,
        vendor=vendor,
        product=product,
        revision=revision,
    )


def sg_inq(device: str, runner: SafeRunner, guard: RealHardwareGuard) -> ScsiInquiry:
    guard.validate()
    if runner.dry_run:
        return parse_sg_inq(SAMPLE_SG_INQ)
    result = runner.run(["sg_inq", device], timeout=30)
    result.raise_on_error()
    return parse_sg_inq(result.stdout)
