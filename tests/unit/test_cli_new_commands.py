"""CLI surface for tree restore, sharded archive, and the mailslot flows.

Two invariants every one of these commands has to keep:

* **stdout is the result and nothing else.** Progress, warnings and the
  chosen-slot narration go to stderr, so `openblade ... | jq` works. Every
  success case here parses stdout as a whole JSON document.
* **the old bare invocations keep working.** `restore` and `archive` became
  Typer groups; `openblade restore --path X --to Y` is what
  scripts/campaign/ runs and must not have moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openblade.cli import main as cli_main

runner = CliRunner()


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI's ~/.openblade at a scratch dir, on the mock backend."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".openblade"
    monkeypatch.setattr(cli_main, "_STATE_DIR", state_dir)
    monkeypatch.setattr(cli_main, "_STATE_PATH", state_dir / "mock_state.json")
    monkeypatch.setattr(cli_main, "_DB_PATH", state_dir / "openblade.db")
    for name in (
        "OPENBLADE_BACKEND",
        "OPENBLADE_REAL_HARDWARE_ENABLED",
        "OPENBLADE_DB_URL",
        "OPENBLADE_CACHE_DIR",
        "OPENBLADE_STAGING_DIR",
        "OPENBLADE_RESTORE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENBLADE_DB_URL", f"sqlite:///{state_dir / 'openblade.db'}")
    monkeypatch.setenv("OPENBLADE_CACHE_DIR", str(state_dir / "cache"))
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(state_dir / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(state_dir / "restore"))
    return home


def invoke(*args: str):
    """Run the CLI with stdout and stderr kept apart."""
    return runner.invoke(cli_main.app, list(args), catch_exceptions=False)


def stdout_json(result) -> object:
    """Parse stdout as one JSON document -- the purity assertion."""
    assert result.stdout.strip(), "command produced no stdout"
    return json.loads(result.stdout)


def bootstrap(cli_home: Path, *, ie_slots: int = 4) -> None:
    result = invoke(
        "mock", "init", "--slots", "6", "--drives", "2",
        "--cartridges", "3", "--ie-slots", str(ie_slots),
    )
    assert result.exit_code == 0, result.output
    for barcode in ("MCK00001", "MCK00002", "MCK00003"):
        dry = invoke("format", "dry-run", "--barcode", barcode)
        token = json.loads(dry.stdout)["token"]
        confirm = invoke("format", "confirm", "--barcode", barcode, "--token", token)
        assert confirm.exit_code == 0, confirm.output


def seed_tree(root: Path) -> dict[str, bytes]:
    files = {
        "alpha/same.txt": b"I am alpha",
        "beta/same.txt": b"I am beta",
        "top.bin": bytes(range(256)) * 20,
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return files


class TestBackwardsCompatibleInvocations:
    def test_bare_restore_still_takes_path_and_to(self, cli_home, tmp_path) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        source.mkdir()
        (source / "one.txt").write_bytes(b"hello")
        invoke("volume-group", "photos")
        invoke("archive", "--volume-group", "photos", "--path", str(source))

        dest = tmp_path / "one.out"
        result = invoke("restore", "--path", "/photos/one.txt", "--to", str(dest))

        assert result.exit_code == 0, result.output
        assert stdout_json(result)["status"] == "completed"
        assert dest.read_bytes() == b"hello"

    def test_bare_archive_still_takes_volume_group_and_path(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        source.mkdir()
        (source / "one.txt").write_bytes(b"hello")

        result = invoke("archive", "--volume-group", "photos", "--path", str(source))

        assert result.exit_code == 0, result.output
        assert stdout_json(result)["status"] == "completed"

    def test_restore_without_options_or_subcommand_says_what_to_do(
        self, cli_home
    ) -> None:
        result = runner.invoke(cli_main.app, ["restore"])
        assert result.exit_code != 0
        assert "--path" in result.output and "restore tree" in result.output


class TestRestoreTree:
    def test_restores_the_tree_and_prints_one_json_summary(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        expected = seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))
        dest = tmp_path / "out"

        result = invoke("restore", "tree", "/photos", "--dest", str(dest))

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        assert payload["status"] == "completed"
        assert payload["filesRestored"] == len(expected)
        assert payload["filesVerified"] == len(expected)
        assert payload["perTapeCounts"]
        for relative, content in expected.items():
            assert (dest / relative).read_bytes() == content

    def test_progress_lines_go_to_stderr_not_stdout(self, cli_home, tmp_path) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))

        result = invoke("restore", "tree", "/photos", "--dest", str(tmp_path / "out"))

        stdout_json(result)  # stdout is one clean document
        assert "[1/3]" in result.stderr
        assert "[1/3]" not in result.stdout

    def test_dry_run_moves_nothing(self, cli_home, tmp_path) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))
        dest = tmp_path / "out"

        result = invoke(
            "restore", "tree", "/photos", "--dest", str(dest), "--dry-run"
        )

        assert result.exit_code == 0
        assert stdout_json(result)["dryRun"] is True
        assert not dest.exists()

    def test_a_failed_file_exits_non_zero_with_the_curated_error(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))
        # Take the tape out of the library, forcibly.
        invoke("mailslot", "export", "MCK00001", "--force")

        result = invoke("restore", "tree", "/photos", "--dest", str(tmp_path / "out"))

        assert result.exit_code == 1
        payload = stdout_json(result)
        assert payload["status"] == "failed"
        assert payload["filesFailed"] == 3
        assert all("offline" in item["error"] for item in payload["failures"])


class TestRestoreFile:
    def test_restores_one_file_into_a_directory(self, cli_home, tmp_path) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        source.mkdir()
        (source / "one.txt").write_bytes(b"hello")
        invoke("archive", "--volume-group", "photos", "--path", str(source))
        dest = tmp_path / "out"
        dest.mkdir()

        result = invoke("restore", "file", "/photos/one.txt", "--dest", str(dest))

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        assert payload["checksumVerified"] is True
        assert payload["sourceBarcodes"] == ["MCK00001"]
        assert (dest / "one.txt").read_bytes() == b"hello"

    def test_unknown_path_is_a_parameter_error_not_a_traceback(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        result = runner.invoke(
            cli_main.app, ["restore", "file", "/nope.txt", "--dest", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "not in the catalog" in result.output


class TestArchiveSharded:
    def test_stripe_across_two_lanes_prints_one_json_document(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)

        result = invoke(
            "archive", "sharded", str(source),
            "--volume-group", "shards", "--mode", "stripe",
            "--lane-barcode", "MCK00001", "--lane-barcode", "MCK00002",
        )

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        assert payload["status"] == "completed"
        assert payload["mode"] == "stripe"
        assert payload["laneBarcodes"] == ["MCK00001", "MCK00002"]
        assert payload["filesArchived"] == 3
        assert payload["blockSizeMb"] == 128  # the API route's default
        assert payload["errors"] == []

    def test_hyphenated_and_underscored_mode_names_both_work(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        source.mkdir()
        (source / "big.bin").write_bytes(bytes(range(256)) * 2000)

        result = invoke(
            "archive", "sharded", str(source),
            "--volume-group", "shards", "--mode", "block-stripe",
            "--lane-barcode", "MCK00001", "--lane-barcode", "MCK00002",
        )

        assert result.exit_code == 0, result.output
        assert stdout_json(result)["mode"] == "block_stripe"

    def test_block_stripe_with_one_lane_is_refused_like_the_api(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)

        result = runner.invoke(
            cli_main.app,
            [
                "archive", "sharded", str(source),
                "--volume-group", "shards", "--mode", "block-stripe",
                "--lane-barcode", "MCK00001",
            ],
        )

        assert result.exit_code != 0
        assert "at least 2 lane barcodes" in result.output

    def test_unknown_mode_names_the_valid_ones(self, cli_home, tmp_path) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        result = runner.invoke(
            cli_main.app,
            [
                "archive", "sharded", str(source),
                "--volume-group", "shards", "--mode", "raid5",
                "--lane-barcode", "MCK00001",
            ],
        )
        assert result.exit_code != 0
        assert "block_stripe" in result.output

    def test_missing_source_is_refused_before_any_media_moves(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        result = runner.invoke(
            cli_main.app,
            [
                "archive", "sharded", str(tmp_path / "nope"),
                "--volume-group", "shards",
                "--lane-barcode", "MCK00001",
            ],
        )
        assert result.exit_code != 0
        # Rich wraps the error box, so match a fragment that cannot straddle a line.
        assert "Source path" in result.output


class TestMailslot:
    def test_list_prints_every_element(self, cli_home) -> None:
        bootstrap(cli_home, ie_slots=4)

        result = invoke("mailslot", "list")

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        assert payload["slotCount"] == 4
        assert payload["occupiedCount"] == 0
        assert [slot["slotId"] for slot in payload["importExportSlots"]] == [7, 8, 9, 10]

    def test_export_then_list_then_import_round_trips(self, cli_home) -> None:
        bootstrap(cli_home)

        exported = invoke("mailslot", "export", "MCK00003")
        assert exported.exit_code == 0, exported.output
        export_payload = stdout_json(exported)
        assert export_payload["destinationSlot"] == 7
        assert export_payload["destinationSlotChosen"] is True

        listing = stdout_json(invoke("mailslot", "list"))
        assert listing["occupied"] == [
            {"slotId": 7, "occupied": True, "barcode": "MCK00003"}
        ]

        imported = invoke("mailslot", "import", "7")
        assert imported.exit_code == 0, imported.output
        import_payload = stdout_json(imported)
        assert import_payload["barcode"] == "MCK00003"
        assert import_payload["destinationSlot"] == 3
        assert stdout_json(invoke("mailslot", "list"))["occupiedCount"] == 0

    def test_the_chosen_slot_is_named_on_stderr_not_mixed_into_stdout(
        self, cli_home
    ) -> None:
        bootstrap(cli_home)
        result = invoke("mailslot", "export", "MCK00003")
        stdout_json(result)
        assert "I/E slot 7" in result.stderr
        assert "chosen automatically" in result.stderr

    def test_import_honours_an_explicit_destination_slot(self, cli_home) -> None:
        bootstrap(cli_home)
        invoke("mailslot", "export", "MCK00003")

        result = invoke("mailslot", "import", "7", "--to-slot", "6")

        payload = stdout_json(result)
        assert payload["destinationSlot"] == 6
        assert payload["destinationSlotChosen"] is False

    def test_export_refuses_a_cartridge_with_data_and_names_it(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))

        result = invoke("mailslot", "export", "MCK00001")

        assert result.exit_code == 1
        assert "Export refused" in result.stderr
        assert "/photos/alpha/same.txt" in result.stderr
        assert "photos" in result.stderr
        # Refused means refused: no result document, and the media did not move.
        assert result.stdout.strip() == ""
        assert stdout_json(invoke("mailslot", "list"))["occupiedCount"] == 0

    def test_export_with_force_reports_what_went_out_of_the_door(
        self, cli_home, tmp_path
    ) -> None:
        bootstrap(cli_home)
        source = tmp_path / "src"
        seed_tree(source)
        invoke("archive", "--volume-group", "photos", "--path", str(source))

        result = invoke("mailslot", "export", "MCK00001", "--force")

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        assert payload["exported"]["carriesData"] is True
        assert payload["exported"]["archivedFilesOnCartridge"] == 3
        assert payload["exported"]["volumeGroup"] == "photos"

    def test_import_from_an_empty_element_exits_one_with_a_typed_error(
        self, cli_home
    ) -> None:
        bootstrap(cli_home)
        result = invoke("mailslot", "import", "8")
        assert result.exit_code == 1
        assert "ImportExportSlotError" in result.stderr
        assert result.stdout.strip() == ""

    def test_a_library_with_no_mailslot_refuses_by_type(self, cli_home) -> None:
        bootstrap(cli_home, ie_slots=0)
        result = invoke("mailslot", "export", "MCK00001")
        assert result.exit_code == 1
        assert "ImportExportSlotError" in result.stderr


def test_mock_state_round_trips_the_mailslot(cli_home) -> None:
    """State is written and read back between processes, so it must persist."""
    bootstrap(cli_home)
    invoke("mailslot", "export", "MCK00002")

    # A fresh command re-reads mock_state.json from scratch.
    payload = stdout_json(invoke("mailslot", "list"))

    assert payload["occupied"] == [
        {"slotId": 7, "occupied": True, "barcode": "MCK00002"}
    ]
