"""Simulator cartridge primitives."""

from dataclasses import dataclass

from openblade.domain.models import Barcode, CartridgeState


@dataclass
class MockCartridge:
    barcode: Barcode
    state: CartridgeState = CartridgeState.IN_SLOT
