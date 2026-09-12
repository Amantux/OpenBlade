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


class DriveCorrelationError(SafetyViolationError):
    """Drive device <-> library drive-element correlation could not be verified.

    Raised when the operator-declared drive serial map disagrees with the serials
    read live from the drives. Acting on an unverified mapping means writing to
    the wrong drive, so this refuses instead of guessing.
    """


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


class UnsafeCatalogPathError(SafetyViolationError):
    """A catalog path cannot be mapped under a restore destination safely.

    Catalog rows are not trusted to be well-formed: ``create_file_record``
    normalises with ``PurePosixPath``, which does not collapse ``..``. A bulk
    restore joins the path onto ``--dest``, so a row that reduces to nothing but
    traversal components would write outside it. Refuse loudly instead.
    """


class MailslotUnsupportedError(OpenBladeError):
    """The active library backend has no import/export (mailslot) station."""


class ImportExportSlotError(OpenBladeError):
    """An import/export element is missing, empty, or already occupied."""


class ExportRefusedError(SafetyViolationError):
    """Refused to export a cartridge that still carries archived data.

    Exporting moves media out of the library: every file instance on that
    cartridge becomes unrestorable until someone physically puts it back. The
    campaign runbook records the unforced version of this -- one unvalidated
    ``dest_slot_id`` ejected a cartridge holding 358 archived files, after which
    ``inventory()`` could not see it at all. Refusing by default (``--force`` to
    override) is the difference between an export and an accident.
    """


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


def safe_job_error(exc: Exception) -> str:
    """Curated failure text for UNAUTHENTICATED surfaces (jobs.error).

    Typed OpenBlade errors carry operator-written messages and pass through.
    Anything else — CommandError (argv + raw mkltfs/mtx stderr), OSError,
    RuntimeError wrapping tool output — must never reach the wire: psycopg-
    style tools echo device paths and command lines. Callers log the full
    exception server-side; this returns only the class name."""
    if isinstance(exc, OpenBladeError):
        return str(exc)
    return f"Job failed ({type(exc).__name__}); see server logs for detail"
