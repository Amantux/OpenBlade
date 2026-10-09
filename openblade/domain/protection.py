"""Protection axes for archived data (roadmap item 10).

Placement, striping and protection are independent axes. Spreading data across
tapes (striping/sharding) is a *performance* choice; it never makes data more
durable on its own. Only redundant copies make a policy "protected".

Pure value objects: no IO, no library access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UnsupportedProtectionError(ValueError):
    """A protection scheme that is modelled but cannot be planned (e.g. erasure coding)."""


class InvalidProtectionPolicyError(ValueError):
    """A protection policy whose axes contradict each other."""


class Placement(StrEnum):
    SINGLE_TAPE = "single_tape"
    STRIPED = "striped"


class StripingMode(StrEnum):
    NONE = "none"
    STRIPE = "stripe"
    BLOCK_STRIPE = "block_stripe"


class ProtectionKind(StrEnum):
    NONE = "none"
    REPLICATION = "replication"
    ERASURE_CODE = "erasure_code"


class FailureDomain(StrEnum):
    DRIVE = "drive"
    TAPE = "tape"
    LIBRARY = "library"


class DurabilityClass(StrEnum):
    UNPROTECTED = "unprotected"
    PERFORMANCE_ONLY = "performance-only"
    PROTECTED = "protected"


@dataclass(frozen=True)
class Striping:
    mode: StripingMode = StripingMode.NONE
    block_size: int | None = None

    def __post_init__(self) -> None:
        if self.mode is StripingMode.BLOCK_STRIPE:
            if self.block_size is None or self.block_size <= 0:
                raise InvalidProtectionPolicyError("block striping requires a positive block_size")
        elif self.block_size is not None:
            raise InvalidProtectionPolicyError("block_size is only valid for block striping")


@dataclass(frozen=True)
class Protection:
    kind: ProtectionKind = ProtectionKind.NONE
    copies: int = 1
    k: int = 0
    m: int = 0

    def __post_init__(self) -> None:
        if self.copies < 1:
            raise InvalidProtectionPolicyError("copies must be >= 1")
        if self.kind is ProtectionKind.ERASURE_CODE and (self.k < 1 or self.m < 1):
            raise InvalidProtectionPolicyError("erasure coding requires k >= 1 and m >= 1")

    @classmethod
    def none(cls) -> Protection:
        return cls()

    @classmethod
    def replication(cls, copies: int) -> Protection:
        return cls(kind=ProtectionKind.REPLICATION, copies=copies)

    @classmethod
    def erasure_code(cls, k: int, m: int) -> Protection:
        return cls(kind=ProtectionKind.ERASURE_CODE, k=k, m=m)

    @property
    def supported(self) -> bool:
        """Erasure coding is modelled only; the planner cannot produce it."""
        return self.kind is not ProtectionKind.ERASURE_CODE

    @property
    def redundant(self) -> bool:
        if self.kind is ProtectionKind.REPLICATION:
            return self.copies >= 2
        return self.kind is ProtectionKind.ERASURE_CODE


@dataclass(frozen=True)
class RestoreQuorum:
    tapes_required: int
    tapes_total: int

    def __post_init__(self) -> None:
        if self.tapes_required < 1 or self.tapes_total < 1:
            raise InvalidProtectionPolicyError("restore quorum counts must be >= 1")
        if self.tapes_required > self.tapes_total:
            raise InvalidProtectionPolicyError("restore quorum cannot exceed total tapes")


@dataclass(frozen=True)
class ProtectionPolicy:
    placement: Placement = Placement.SINGLE_TAPE
    striping: Striping = field(default_factory=Striping)
    protection: Protection = field(default_factory=Protection)
    failure_domain: FailureDomain = FailureDomain.TAPE
    quorum: RestoreQuorum | None = None

    def __post_init__(self) -> None:
        striped = self.striping.mode is not StripingMode.NONE
        if striped != (self.placement is Placement.STRIPED):
            raise InvalidProtectionPolicyError("striping mode must match STRIPED placement")

    @property
    def is_protected(self) -> bool:
        return self.protection.redundant

    @property
    def durability_class(self) -> DurabilityClass:
        if self.is_protected:
            return DurabilityClass.PROTECTED
        if self.striping.mode is not StripingMode.NONE:
            return DurabilityClass.PERFORMANCE_ONLY
        return DurabilityClass.UNPROTECTED

    def require_plannable(self) -> None:
        """Raise if the planner cannot realise this policy."""
        if not self.protection.supported:
            raise UnsupportedProtectionError(
                f"protection '{self.protection.kind.value}' is modelled but not supported"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "placement": self.placement.value,
            "striping": {"mode": self.striping.mode.value, "block_size": self.striping.block_size},
            "protection": {
                "kind": self.protection.kind.value,
                "copies": self.protection.copies,
                "k": self.protection.k,
                "m": self.protection.m,
            },
            "failure_domain": self.failure_domain.value,
            "quorum": None
            if self.quorum is None
            else {
                "tapes_required": self.quorum.tapes_required,
                "tapes_total": self.quorum.tapes_total,
            },
            "durability_class": self.durability_class.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtectionPolicy:
        striping = data.get("striping") or {}
        protection = data.get("protection") or {}
        quorum = data.get("quorum")
        return cls(
            placement=Placement(data.get("placement", Placement.SINGLE_TAPE.value)),
            striping=Striping(
                mode=StripingMode(striping.get("mode", StripingMode.NONE.value)),
                block_size=striping.get("block_size"),
            ),
            protection=Protection(
                kind=ProtectionKind(protection.get("kind", ProtectionKind.NONE.value)),
                copies=int(protection.get("copies", 1)),
                k=int(protection.get("k", 0)),
                m=int(protection.get("m", 0)),
            ),
            failure_domain=FailureDomain(data.get("failure_domain", FailureDomain.TAPE.value)),
            quorum=None
            if quorum is None
            else RestoreQuorum(int(quorum["tapes_required"]), int(quorum["tapes_total"])),
        )


def protection_from_pool(pool: Any) -> ProtectionPolicy:
    """Map a legacy ``NasPool.replication_factor`` onto the protection axis.

    ``replication_factor`` stays the source of truth for existing pools; this only
    expresses it as ``Protection.REPLICATION(copies=n)``. Placement is single-tape:
    the legacy field says nothing about striping.
    """
    copies = max(1, int(getattr(pool, "replication_factor", 1) or 1))
    protection = Protection.replication(copies) if copies >= 2 else Protection.none()
    return ProtectionPolicy(protection=protection)
