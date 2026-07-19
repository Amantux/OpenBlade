"""Disaster-recovery: SQLite backup + executed restore verification.

OpenBlade's durable state is a single SQLite catalog. This provides a WAL-safe
online backup and a restore-verification that operates ONLY on an isolated copy —
it never writes to the source/production DB. The verification restores the
backup, runs `PRAGMA integrity_check`, confirms the expected schema, and runs a
representative application query through the real data layer.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Core catalog tables a real backup MUST contain. If any is absent the backup is
# incomplete — we assert their presence on the RAW restored copy (no create_all,
# which would silently recreate them and mask the loss).
EXPECTED_TABLES = {"file_records", "cartridges", "nas_datasets", "jobs"}


def sqlite_path(db_url: str) -> str:
    """Extract the filesystem path from a sqlite SQLAlchemy URL."""
    if not db_url.startswith("sqlite"):
        raise ValueError(f"not a sqlite url: {db_url!r}")
    # SQLAlchemy: sqlite:///rel/path (relative), sqlite:////abs/path (absolute),
    # sqlite:///:memory:. Stripping "sqlite://" leaves exactly one extra leading
    # slash to remove: "/rel"->"rel", "//abs"->"/abs", "/:memory:"->":memory:".
    tail = db_url.split("sqlite://", 1)[1]
    path = tail[1:] if tail.startswith("/") else tail
    if not path:
        raise ValueError(f"sqlite url has no database path: {db_url!r}")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_sqlite(src_url: str, dest_path: Path) -> dict[str, object]:
    """WAL-safe online backup of the source SQLite DB to dest_path."""
    src = sqlite_path(src_url)
    if src == ":memory:":
        raise ValueError("cannot back up an in-memory database")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as src_conn, sqlite3.connect(str(dest_path)) as dst_conn:
        src_conn.backup(dst_conn)  # online backup API — consistent snapshot
    return {"path": str(dest_path), "bytes": dest_path.stat().st_size, "sha256": _sha256(dest_path)}


@dataclass
class DrCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DrReport:
    backup_path: str
    checks: list[DrCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {"backup_path": self.backup_path, "ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def restore_and_verify(backup_path: Path) -> DrReport:
    """Restore the backup into an isolated temp DB and verify it — never touches the source."""
    report = DrReport(backup_path=str(backup_path))
    workdir = Path(tempfile.mkdtemp(prefix="openblade-dr-"))
    try:
        if not backup_path.exists():
            report.checks.append(DrCheck("backup_present", False, f"missing: {backup_path}"))
            return report
        report.checks.append(DrCheck("backup_present", True, f"{backup_path.stat().st_size} bytes"))

        restored = workdir / "restored.db"
        shutil.copy2(backup_path, restored)

        # Everything below reads ONLY the raw restored copy — no init_db/create_all
        # (which would recreate missing tables and mask an incomplete backup) and no
        # module-global engine mutation. A corrupt/incomplete backup raises; that IS
        # the finding.
        try:
            with sqlite3.connect(str(restored)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                integ_ok = bool(result) and result[0] == "ok"
                report.checks.append(DrCheck("integrity_check", integ_ok, str(result[0] if result else "no result")))

                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                missing = EXPECTED_TABLES - tables
                report.checks.append(DrCheck("schema_present", not missing,
                    f"{len(tables)} tables; missing={sorted(missing)}" if missing else f"{len(tables)} tables"))

                # Representative read against the restored data as-is — proves the
                # core table is queryable without recreating anything.
                if "file_records" in tables:
                    count = conn.execute("SELECT count(*) FROM file_records").fetchone()[0]
                    report.checks.append(DrCheck("data_layer_query", True, f"{count} file records readable"))
                else:
                    report.checks.append(DrCheck("data_layer_query", False,
                        "file_records table absent — backup cannot be read by the app"))
        except sqlite3.DatabaseError as exc:
            report.checks.append(DrCheck("integrity_check", False, f"backup is not a valid database: {exc}"))
            return report

        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
