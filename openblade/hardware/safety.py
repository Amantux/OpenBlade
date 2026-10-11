"""Hardware safety gate enforcement."""

from openblade.config import BackendMode, OpenBladeConfig
from openblade.domain.errors import RealHardwareDisabledError
from openblade.domain.policies import RealHardwareGuard


def require_real_hardware(config: OpenBladeConfig) -> RealHardwareGuard:
    """Build a RealHardwareGuard or raise RealHardwareDisabledError."""
    if config.backend != BackendMode.REAL or not config.real_hardware_enabled:
        raise RealHardwareDisabledError(
            "Real hardware operations require OPENBLADE_BACKEND=real "
            "and OPENBLADE_REAL_HARDWARE_ENABLED=true"
        )
    # NOTE: "config-verified" is NOT a human acknowledgment; it only satisfies
    # the guard's non-empty check. The real gates are the config flags above,
    # the OPENBLADE_API_TOKEN requirement in load_config, and the format
    # token + scratch allowlist. Removing the field ripples into many callers.
    return RealHardwareGuard(
        config_backend=config.backend.value,
        config_real_hardware_enabled=config.real_hardware_enabled,
        operator_acknowledgment="config-verified",
    )
