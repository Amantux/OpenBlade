"""Inventory helpers."""

from __future__ import annotations

from openblade.catalog.repository import CatalogRepository
from openblade.simulator.library import MockLibraryBackend


class InventoryService:
    def __init__(self, library: MockLibraryBackend) -> None:
        self.library = library

    def snapshot(self):
        return self.library.inventory()


def run_inventory_job(library: MockLibraryBackend, catalog: CatalogRepository) -> dict[str, object]:
    """Sync library inventory into the cartridge catalog."""
    inventory = library.inventory()
    seen: set[str] = set()
    synced = 0
    for slot in inventory.slots:
        if slot.barcode is None:
            continue
        cartridge = catalog.add_cartridge(str(slot.barcode))
        cartridge.state = "in_slot"
        seen.add(cartridge.barcode)
        synced += 1
    for drive in inventory.drives:
        if drive.barcode is None:
            continue
        cartridge = catalog.add_cartridge(str(drive.barcode))
        cartridge.state = "in_drive"
        seen.add(cartridge.barcode)
        synced += 1
    states = getattr(library, "_cartridge_states", {})
    for barcode, state in states.items():
        cartridge = catalog.add_cartridge(barcode)
        cartridge.state = state.value if hasattr(state, "value") else str(state)
        seen.add(barcode)
    catalog.session.commit()
    return {"cartridges_synced": synced, "barcodes": sorted(seen)}
