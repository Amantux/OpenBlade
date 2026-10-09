from __future__ import annotations

from types import SimpleNamespace

import pytest

from openblade.domain.protection import (
    DurabilityClass,
    FailureDomain,
    InvalidProtectionPolicyError,
    Placement,
    Protection,
    ProtectionPolicy,
    RestoreQuorum,
    Striping,
    StripingMode,
    UnsupportedProtectionError,
    protection_from_pool,
)

_STRIPINGS = [
    (Placement.SINGLE_TAPE, Striping()),
    (Placement.STRIPED, Striping(StripingMode.STRIPE)),
    (Placement.STRIPED, Striping(StripingMode.BLOCK_STRIPE, block_size=1 << 20)),
]
_PROTECTIONS = [
    (Protection.none(), False),
    (Protection.replication(1), False),
    (Protection.replication(2), True),
    (Protection.replication(3), True),
    (Protection.erasure_code(4, 2), True),
]


@pytest.mark.parametrize("domain", list(FailureDomain))
@pytest.mark.parametrize(("placement", "striping"), _STRIPINGS)
@pytest.mark.parametrize(("protection", "expected"), _PROTECTIONS)
def test_is_protected_for_every_combination(
    domain: FailureDomain,
    placement: Placement,
    striping: Striping,
    protection: Protection,
    expected: bool,
) -> None:
    policy = ProtectionPolicy(
        placement=placement, striping=striping, protection=protection, failure_domain=domain
    )
    assert policy.is_protected is expected
    assert (policy.durability_class is DurabilityClass.PROTECTED) is expected


@pytest.mark.parametrize("mode", [StripingMode.STRIPE, StripingMode.BLOCK_STRIPE])
@pytest.mark.parametrize("copies", [1])
def test_striping_never_implies_protection(mode: StripingMode, copies: int) -> None:
    block = 4096 if mode is StripingMode.BLOCK_STRIPE else None
    policy = ProtectionPolicy(
        placement=Placement.STRIPED,
        striping=Striping(mode, block_size=block),
        protection=Protection.replication(copies),
    )
    assert policy.is_protected is False
    assert policy.durability_class is DurabilityClass.PERFORMANCE_ONLY


def test_quorum_cannot_exceed_total() -> None:
    with pytest.raises(InvalidProtectionPolicyError):
        RestoreQuorum(tapes_required=3, tapes_total=2)
    assert RestoreQuorum(2, 2).tapes_required == 2


def test_striping_must_match_placement() -> None:
    with pytest.raises(InvalidProtectionPolicyError):
        ProtectionPolicy(placement=Placement.SINGLE_TAPE, striping=Striping(StripingMode.STRIPE))
    with pytest.raises(InvalidProtectionPolicyError):
        Striping(StripingMode.BLOCK_STRIPE)


def test_erasure_code_is_modelled_but_not_plannable() -> None:
    policy = ProtectionPolicy(protection=Protection.erasure_code(4, 2))
    assert policy.protection.supported is False
    with pytest.raises(UnsupportedProtectionError):
        policy.require_plannable()
    ProtectionPolicy(protection=Protection.replication(2)).require_plannable()


@pytest.mark.parametrize(("factor", "copies", "protected"), [(1, 1, False), (2, 2, True)])
def test_protection_from_pool(factor: int, copies: int, protected: bool) -> None:
    policy = protection_from_pool(SimpleNamespace(replication_factor=factor))
    assert policy.protection.copies == copies
    assert policy.is_protected is protected


def test_policy_dict_round_trip() -> None:
    policy = ProtectionPolicy(
        placement=Placement.STRIPED,
        striping=Striping(StripingMode.BLOCK_STRIPE, block_size=8192),
        protection=Protection.replication(2),
        failure_domain=FailureDomain.LIBRARY,
        quorum=RestoreQuorum(2, 4),
    )
    assert ProtectionPolicy.from_dict(policy.to_dict()) == policy
