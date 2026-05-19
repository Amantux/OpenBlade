"""Typed domain errors for OpenBlade."""


class OpenBladeError(Exception):
    """Base error."""


class InvalidStateTransitionError(OpenBladeError):
    """Attempted an invalid state transition."""


class SafetyViolationError(OpenBladeError):
    """A safety gate was violated."""


class RealHardwareDisabledError(SafetyViolationError):
    """Real hardware not enabled in config."""


class BarcodeMismatchError(SafetyViolationError):
    """Barcode in confirmation does not match device."""


class DriveOccupiedError(OpenBladeError):
    """Drive is already in use."""


class SlotOccupiedError(OpenBladeError):
    """Target slot is occupied."""


class SlotEmptyError(OpenBladeError):
    """Source slot is empty."""


class TapeMountedError(SafetyViolationError):
    """Cannot unload a mounted tape."""


class DriveBusyError(OpenBladeError):
    """Drive is busy."""


class ChangerBusyError(OpenBladeError):
    """Changer is busy with another operation."""


class NoScratchMediaError(OpenBladeError):
    """No scratch/blank media available."""


class TapeFullError(OpenBladeError):
    """Tape has insufficient free space."""


class ChecksumMismatchError(OpenBladeError):
    """Checksum verification failed."""


class CartridgeNotFoundError(OpenBladeError):
    """Cartridge not found in library."""


class CartridgeOfflineError(OpenBladeError):
    """Cartridge is exported/offline."""


class FileNotFoundError(OpenBladeError):  # noqa: A001
    """File not found in catalog."""


class JobNotFoundError(OpenBladeError):
    """Job not found."""


class FormatRequiresConfirmationError(SafetyViolationError):
    """Format operation requires explicit confirmation."""


class SimulatedWriteFailure(OpenBladeError):
    """Injected write failure (simulator only)."""


class SimulatedRobotTimeout(OpenBladeError):
    """Injected robot timeout (simulator only)."""


class SimulatedMountFailure(OpenBladeError):
    """Injected mount failure (simulator only)."""
