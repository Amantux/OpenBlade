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
    return RealHardwareGuard(
        config_backend=config.backend.value,
        config_real_hardware_enabled=config.real_hardware_enabled,
        operator_acknowledgment="config-verified",
    )
