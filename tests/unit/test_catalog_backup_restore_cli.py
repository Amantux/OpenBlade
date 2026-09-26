"""CLI surface for `openblade catalog backup` and `catalog restore-backup`.

Covers: the online-backup round trip (including a concurrent writer, which is
the entire point of using sqlite3.Connection.backup() instead of a file copy),
`--keep` pruning, both destructive-restore refusal guards, and refusing a
corrupt backup before the live catalog is ever touched.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import threading
import time
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
    return runner.invoke(cli_main.app, list(args), catch_exceptions=False)


def stdout_json(result) -> object:
    assert result.stdout.strip(), "command produced no stdout"
    return json.loads(result.stdout)


def _init_live_db(cli_home: Path) -> tuple[Path, str]:
    """Force the live catalog DB into existence and return (db_path, canary id).

    The canary is the "seed" volume group's own id. `_get_context()` (called by
    every CLI command, including a refused one) legitimately reseeds admin/
    service accounts and an inventory job on each invocation, so comparing the
    whole file's bytes before/after a refusal is not a valid "untouched" check
    -- it would fail even with no bug present. Checking that this specific,
    pre-existing row survives unchanged is the right invariant: a real restore
    (or any bug that let one slip past a guard) would replace or lose it.
    """
    result = invoke("volume-group", "seed")
    assert result.exit_code == 0, result.output
    canary_id = stdout_json(result)["id"]
    assert isinstance(canary_id, str)
    return cli_home / ".openblade" / "openblade.db", canary_id


def _assert_canary_untouched(db_path: Path, canary_id: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT name FROM volume_groups WHERE id = ?", (canary_id,)).fetchone()
    assert row is not None, "canary volume group is gone -- the live catalog was replaced"
    assert row[0] == "seed"


class TestBackupRoundTrip:
    def test_backup_survives_a_concurrent_writer_and_restores_cleanly(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        db_path, _canary_id = _init_live_db(cli_home)
        stop = threading.Event()
        errors: list[BaseException] = []
        committed_ids: list[str] = []

        def writer() -> None:
            # Deliberately gentle: this repo's SQLite catalog is not in WAL
            # mode, so a writer and a concurrent reader/writer contend for the
            # same file lock like any default-journal-mode SQLite. A tight
            # write loop starves `catalog backup`'s own setup queries (which
            # is a real limitation of rollback-journal SQLite, not something
            # this test is trying to prove) and makes the test flaky rather
            # than demonstrating the property under test: that an online
            # *backup* mid-write is safe, not that arbitrary concurrent
            # writers never see `database is locked`.
            conn = sqlite3.connect(str(db_path), timeout=30)
            try:
                i = 0
                while not stop.is_set():
                    job_id = f"writer-job-{i}"
                    try:
                        conn.execute(
                            "INSERT INTO jobs (id, job_type, state, metadata_json, "
                            "error, created_at, updated_at) VALUES (?, 'archive', "
                            "'completed', '{}', NULL, datetime('now'), datetime('now'))",
                            (job_id,),
                        )
                        conn.commit()
                    except sqlite3.OperationalError:
                        conn.rollback()
                        time.sleep(0.05)
                        continue
                    committed_ids.append(job_id)
                    i += 1
                    time.sleep(0.05)
            except BaseException as exc:  # noqa: BLE001 — worker thread's ONLY channel back to the asserting test; anything narrower hides the failure  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                conn.close()

        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.1)  # let the writer get going before the backup starts

        backup_dest = tmp_path / "backups"
        result = invoke("catalog", "backup", "--dest", str(backup_dest))

        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert not errors, f"writer thread raised: {errors}"
        assert result.exit_code == 0, result.output
        assert len(committed_ids) > 0, "writer never got a row committed"

        payload = stdout_json(result)
        backup_file = Path(payload["backupFile"])
        assert backup_file.exists()
        assert backup_file.suffix == ".gz"

        # The backup is a point-in-time snapshot taken *during* the writer loop:
        # it must be internally consistent (passes integrity_check) and must not
        # contain more committed rows than were ever committed -- a torn/partial
        # read would show up as a corrupt file or a phantom row, not as "some
        # subset of what was actually committed".
        with gzip.open(backup_file, "rb") as gz_src, open(tmp_path / "check.db", "wb") as dst:
            shutil.copyfileobj(gz_src, dst)
        with sqlite3.connect(str(tmp_path / "check.db")) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            rows = {
                row[0]
                for row in conn.execute(
                    "SELECT id FROM jobs WHERE id LIKE 'writer-job-%'"
                ).fetchall()
            }
        assert rows <= set(committed_ids)

        # Now actually restore it over the live DB and confirm the destructive
        # path leaves a healthy, queryable catalog behind.
        restore_result = invoke(
            "catalog", "restore-backup", str(backup_file), "--confirm-db-path", str(db_path)
        )
        assert restore_result.exit_code == 0, restore_result.output
        restore_payload = stdout_json(restore_result)
        assert restore_payload["restored"] is True
        checks = {c["name"]: c["ok"] for c in restore_payload["checks"]}
        assert all(checks.values()), checks

        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            restored_rows = {
                row[0]
                for row in conn.execute(
                    "SELECT id FROM jobs WHERE id LIKE 'writer-job-%'"
                ).fetchall()
            }
        assert restored_rows == rows

    def test_keep_prunes_older_backups(self, cli_home: Path, tmp_path: Path) -> None:
        _init_live_db(cli_home)
        backup_dest = tmp_path / "backups"
        backup_files = []
        for _ in range(5):
            result = invoke("catalog", "backup", "--dest", str(backup_dest), "--keep", "3")
            assert result.exit_code == 0, result.output
            backup_files.append(Path(stdout_json(result)["backupFile"]))
            time.sleep(1.1)  # filenames are second-resolution timestamps

        remaining = sorted(backup_dest.glob("openblade-*.db.gz"))
        assert len(remaining) == 3
        # The three most-recently-created backups survive; earlier ones are gone.
        assert set(remaining) == set(backup_files[-3:])
        for pruned in backup_files[:-3]:
            assert not pruned.exists()

    def test_keep_zero_still_keeps_the_backup_just_taken(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        # Regression: `--keep 0` used to mean "prune down to 0 backups
        # total", which deleted the file this very invocation just wrote and
        # then crashed reporting its size. `--keep N` means "keep N OLDER
        # backups alongside the one just taken" -- the backup you just made
        # is never a candidate for its own prune.
        _init_live_db(cli_home)
        backup_dest = tmp_path / "backups"

        result = invoke("catalog", "backup", "--dest", str(backup_dest), "--keep", "0")

        assert result.exit_code == 0, result.output
        payload = stdout_json(result)
        backup_file = Path(payload["backupFile"])
        assert backup_file.exists()
        assert payload["pruned"] == []
        assert payload["retained"] == 1
        assert sorted(backup_dest.glob("openblade-*.db.gz")) == [backup_file]

    def test_keep_zero_prunes_all_older_backups(self, cli_home: Path, tmp_path: Path) -> None:
        _init_live_db(cli_home)
        backup_dest = tmp_path / "backups"
        first = invoke("catalog", "backup", "--dest", str(backup_dest), "--keep", "99")
        assert first.exit_code == 0, first.output
        first_file = Path(stdout_json(first)["backupFile"])
        time.sleep(1.1)

        second = invoke("catalog", "backup", "--dest", str(backup_dest), "--keep", "0")

        assert second.exit_code == 0, second.output
        payload = stdout_json(second)
        second_file = Path(payload["backupFile"])
        assert str(first_file) in payload["pruned"]
        assert not first_file.exists()
        assert second_file.exists()
        assert sorted(backup_dest.glob("openblade-*.db.gz")) == [second_file]


class TestRestoreBackupStaging:
    def test_stages_the_decompressed_backup_next_to_the_live_db(
        self, cli_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: the decompression workdir used to be created in the
        # system temp dir (plain `tempfile.mkdtemp()`), which is frequently a
        # different filesystem/mount from the live catalog's directory (e.g.
        # the container's writable layer vs. a `/data` volume). The final
        # `os.replace()` is only atomic -- or even possible -- when both
        # paths share a filesystem, so the workdir must be created WITH
        # `dir=<live db's parent>`. This would still pass on a dev machine
        # where both happen to be under /tmp, so we assert the call's `dir`
        # kwarg directly rather than relying on an actual cross-device mount.
        import tempfile as tempfile_module

        db_path, _canary_id = _init_live_db(cli_home)
        backup_dest = tmp_path / "backups"
        backup = invoke("catalog", "backup", "--dest", str(backup_dest))
        backup_file = Path(stdout_json(backup)["backupFile"])

        seen_dirs: list[str | None] = []
        real_mkdtemp = tempfile_module.mkdtemp

        def spy_mkdtemp(*args: object, **kwargs: object) -> str:
            seen_dirs.append(kwargs.get("dir"))  # type: ignore[arg-type]
            return real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(tempfile_module, "mkdtemp", spy_mkdtemp)

        result = invoke(
            "catalog", "restore-backup", str(backup_file), "--confirm-db-path", str(db_path)
        )

        assert result.exit_code == 0, result.output
        assert str(db_path.parent) in seen_dirs


class TestRestoreBackupGuards:
    def test_refuses_when_confirm_path_does_not_match_live_db(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        db_path, canary_id = _init_live_db(cli_home)
        backup_dest = tmp_path / "backups"
        backup = invoke("catalog", "backup", "--dest", str(backup_dest))
        backup_file = Path(stdout_json(backup)["backupFile"])

        result = invoke(
            "catalog",
            "restore-backup",
            str(backup_file),
            "--confirm-db-path",
            str(tmp_path / "not-the-live-db.db"),
        )

        assert result.exit_code != 0
        assert "Refusing restore" in result.output
        _assert_canary_untouched(db_path, canary_id)

    def test_refuses_while_a_job_is_pending_or_running(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        db_path, canary_id = _init_live_db(cli_home)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO jobs (id, job_type, state, metadata_json, error, "
                "created_at, updated_at) VALUES ('still-running', 'restore', "
                "'running', '{}', NULL, datetime('now'), datetime('now'))"
            )
            conn.commit()

        backup_dest = tmp_path / "backups"
        backup = invoke("catalog", "backup", "--dest", str(backup_dest))
        backup_file = Path(stdout_json(backup)["backupFile"])

        result = invoke(
            "catalog", "restore-backup", str(backup_file), "--confirm-db-path", str(db_path)
        )

        assert result.exit_code != 0
        assert "Refusing restore" in result.output
        assert "running" in result.output
        _assert_canary_untouched(db_path, canary_id)
        with sqlite3.connect(str(db_path)) as conn:
            state = conn.execute("SELECT state FROM jobs WHERE id = 'still-running'").fetchone()
        assert state == ("running",)

    def test_refuses_a_corrupt_gzip_before_touching_the_live_db(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        db_path, canary_id = _init_live_db(cli_home)

        bad_backup = tmp_path / "corrupt.db.gz"
        bad_backup.write_bytes(b"not actually gzip")

        result = invoke(
            "catalog", "restore-backup", str(bad_backup), "--confirm-db-path", str(db_path)
        )

        assert result.exit_code != 0
        assert "Corrupt backup file" in result.output
        _assert_canary_untouched(db_path, canary_id)

    def test_refuses_a_backup_that_fails_integrity_verification(
        self, cli_home: Path, tmp_path: Path
    ) -> None:
        db_path, canary_id = _init_live_db(cli_home)

        # A well-formed gzip stream around bytes that are not a valid SQLite
        # database at all -- distinct from the "not gzip" corruption case above.
        bad_backup = tmp_path / "bad-content.db.gz"
        with gzip.open(bad_backup, "wb") as gz:
            gz.write(b"this is not a sqlite database" * 10)

        result = invoke(
            "catalog", "restore-backup", str(bad_backup), "--confirm-db-path", str(db_path)
        )

        assert result.exit_code != 0
        assert "failed integrity verification" in result.output
        _assert_canary_untouched(db_path, canary_id)
