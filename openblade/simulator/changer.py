"""Simulator changer state."""

from dataclasses import dataclass

from openblade.domain.models import ChangerState


@dataclass
class MockChanger:
    state: ChangerState = ChangerState.IDLE
