"""``/mailslot/*`` -- the HTTP half of ``openblade mailslot``.

The interesting assertions are the refusal ones: the API must not let archived
data walk out of the library on an unforced request, and it must say what would
have left. See ``openblade/catalog/export_policy.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig

SERVICE_TOKEN = "openblade-controller-dev-token-do-not-expose"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'mailslot.db'}"))
    reset_context(context)
    return TestClient(app)


def _data_barcodes(limit: int) -> list[str]:
    context = get_context()
    return [
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    ][:limit]


def _admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def _format_and_assign(client: TestClient, volume_group: str, barcode: str) -> None:
    auth_headers = _admin_auth_headers(client)
    assert client.post("/volume-groups/", json={"name": volume_group}).status_code == 201
    assert (
        client.post(f"/volume-groups/{volume_group}/assign", json={"barcode": barcode}).status_code
        == 200
    )
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run", headers=auth_headers)
    assert dry_run.status_code == 200
    assert (
        client.post(
            "/cartridges/format/confirm",
            json={"barcode": barcode, "token": dry_run.json()["token"]},
            headers={**auth_headers, "X-Openblade-Service-Token": SERVICE_TOKEN},
        ).status_code
        == 200
    )


def _archive_one_file(
    client: TestClient, source_dir: Path, volume_group: str, file_name: str
) -> None:
    source_dir.mkdir()
    (source_dir / file_name).write_text("mailslot export policy fixture")
    response = client.post(
        "/archive/",
        json={"source_path": str(source_dir), "volume_group": volume_group},
    )
    assert response.status_code == 202


def test_slots_lists_every_import_export_element(client: TestClient) -> None:
    response = client.get("/mailslot/slots")
    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    # The i3 profile declares two I/E elements; the listing reports all of them,
    # empty ones included, because "empty" is the answer to "can I export?".
    assert payload["slotCount"] == len(payload["importExportSlots"]) == 2
    assert payload["occupiedCount"] == 0
    assert all(
        slot["occupied"] is False and slot["barcode"] is None
        for slot in payload["importExportSlots"]
    )


def test_export_then_import_round_trips_a_scratch_cartridge(client: TestClient) -> None:
    barcode = _data_barcodes(limit=1)[0]

    exported = client.post("/mailslot/export", json={"barcode": barcode})
    assert exported.status_code == 200, exported.text
    export_payload = exported.json()
    assert export_payload["barcode"] == barcode
    assert export_payload["source"] == "storage_slot"
    assert export_payload["destination"] == "import_export_slot"
    # Nobody named a destination element, so the service picked one and says so.
    assert export_payload["destinationSlotChosen"] is True
    ie_slot = export_payload["destinationSlot"]

    occupied = client.get("/mailslot/slots").json()
    assert occupied["occupiedCount"] == 1
    assert [slot["barcode"] for slot in occupied["importExportSlots"] if slot["occupied"]] == [
        barcode
    ]

    imported = client.post("/mailslot/import", json={"ie_slot": ie_slot})
    assert imported.status_code == 200, imported.text
    import_payload = imported.json()
    assert import_payload["barcode"] == barcode
    assert import_payload["source"] == "import_export_slot"
    assert import_payload["sourceSlot"] == ie_slot
    assert import_payload["destinationSlotChosen"] is True
    assert client.get("/mailslot/slots").json()["occupiedCount"] == 0


def test_import_honours_an_explicit_target_slot(client: TestClient) -> None:
    barcode = _data_barcodes(limit=1)[0]
    ie_slot = client.post("/mailslot/export", json={"barcode": barcode}).json()["destinationSlot"]

    context = get_context()
    empty_slot = next(
        slot.slot_id for slot in context.library.inventory().slots if slot.barcode is None
    )
    imported = client.post("/mailslot/import", json={"ie_slot": ie_slot, "to_slot": empty_slot})
    assert imported.status_code == 200, imported.text
    assert imported.json()["destinationSlot"] == empty_slot
    assert imported.json()["destinationSlotChosen"] is False


def test_import_from_an_empty_element_is_a_409_naming_the_element(client: TestClient) -> None:
    response = client.post("/mailslot/import", json={"ie_slot": 51})
    assert response.status_code == 409
    assert "51" in response.json()["detail"]


def test_import_from_an_unknown_element_says_which_exist(client: TestClient) -> None:
    response = client.post("/mailslot/import", json={"ie_slot": 9999})
    assert response.status_code == 409
    assert "9999" in response.json()["detail"]
    assert "does not exist" in response.json()["detail"]


def test_export_preview_reports_what_would_leave(client: TestClient, tmp_path: Path) -> None:
    barcode = _data_barcodes(limit=1)[0]
    _format_and_assign(client, "photos", barcode)
    _archive_one_file(client, tmp_path / "photos-source", "photos", "a.txt")

    response = client.get(f"/mailslot/export-preview/{barcode}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["barcode"] == barcode
    assert payload["carriesData"] is True
    assert payload["archivedFilesOnCartridge"] >= 1
    assert payload["bytesOnCartridge"] > 0
    assert payload["volumeGroup"] == "photos"
    assert any(path.endswith("a.txt") for path in payload["samplePaths"])
    # A preview moves nothing: the cartridge is still in storage afterwards.
    assert client.get("/mailslot/slots").json()["occupiedCount"] == 0


def test_export_preview_of_a_scratch_cartridge_carries_no_data(client: TestClient) -> None:
    barcode = _data_barcodes(limit=1)[0]
    payload = client.get(f"/mailslot/export-preview/{barcode}").json()
    assert payload["carriesData"] is False
    assert payload["archivedFilesOnCartridge"] == 0
    assert payload["samplePaths"] == []


def test_export_of_a_cartridge_carrying_data_is_refused_and_names_it(
    client: TestClient, tmp_path: Path
) -> None:
    barcode = _data_barcodes(limit=1)[0]
    _format_and_assign(client, "photos", barcode)
    _archive_one_file(client, tmp_path / "photos-source", "photos", "a.txt")

    response = client.post("/mailslot/export", json={"barcode": barcode})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert barcode in detail
    assert "still carries archived data" in detail
    assert "photos" in detail
    assert "a.txt" in detail
    # Refused means nothing moved: the I/E station is still empty.
    assert client.get("/mailslot/slots").json()["occupiedCount"] == 0


def test_typed_confirmation_exports_the_same_cartridge(client: TestClient, tmp_path: Path) -> None:
    barcode = _data_barcodes(limit=1)[0]
    _format_and_assign(client, "photos", barcode)
    _archive_one_file(client, tmp_path / "photos-source", "photos", "a.txt")
    assert client.post("/mailslot/export", json={"barcode": barcode}).status_code == 409

    forced = client.post("/mailslot/export", json={"barcode": barcode, "confirmBarcode": barcode})
    assert forced.status_code == 200, forced.text
    payload = forced.json()
    assert payload["barcode"] == barcode
    # The response carries the assessment, so an audit of a forced export still
    # records what left the library.
    assert payload["exported"]["carriesData"] is True
    assert payload["exported"]["archivedFilesOnCartridge"] >= 1
    assert client.get("/mailslot/slots").json()["occupiedCount"] == 1


def test_export_of_an_unknown_barcode_is_a_404(client: TestClient) -> None:
    response = client.post("/mailslot/export", json={"barcode": "NOSUCH1L8"})
    assert response.status_code == 404
    assert "NOSUCH1L8" in response.json()["detail"]


def test_export_into_an_occupied_element_is_refused(client: TestClient) -> None:
    first, second = _data_barcodes(limit=2)
    occupied_slot = client.post("/mailslot/export", json={"barcode": first}).json()[
        "destinationSlot"
    ]

    response = client.post("/mailslot/export", json={"barcode": second, "ie_slot": occupied_slot})
    assert response.status_code == 409
    assert first in response.json()["detail"]


def test_export_refuses_once_every_element_is_full(client: TestClient) -> None:
    barcodes = _data_barcodes(limit=3)
    slot_count = client.get("/mailslot/slots").json()["slotCount"]
    for barcode in barcodes[:slot_count]:
        assert client.post("/mailslot/export", json={"barcode": barcode}).status_code == 200

    response = client.post("/mailslot/export", json={"barcode": barcodes[slot_count]})
    assert response.status_code == 409
    assert "empty the mailslot" in response.json()["detail"]


def test_export_preview_of_an_unknown_barcode_is_a_404_not_a_cheerful_zero(
    client: TestClient,
) -> None:
    # `assess_export` answers "nothing on it" for a barcode that does not exist,
    # which is true and useless: next to an Export button a typo would read as
    # "safe to export".
    response = client.get("/mailslot/export-preview/NOSUCH1L8")
    assert response.status_code == 404
    assert "NOSUCH1L8" in response.json()["detail"]


def test_a_mistyped_confirm_barcode_is_refused(client: TestClient, tmp_path: Path) -> None:
    """Typed confirmation means EXACT match — a near-miss must not export.
    (This is the API-side twin of the CLI's --confirm-barcode guard.)"""
    barcode = _data_barcodes(limit=1)[0]
    _format_and_assign(client, "photos", barcode)
    _archive_one_file(client, tmp_path / "photos-source", "photos", "a.txt")
    wrong = client.post(
        "/mailslot/export", json={"barcode": barcode, "confirmBarcode": barcode.lower()}
    )
    assert wrong.status_code == 409, wrong.text
    # Nothing moved on a near-miss.
    assert client.get("/mailslot/slots").json()["occupiedCount"] == 0
