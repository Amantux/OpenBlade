from pathlib import Path

from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.jobs.verify import run_verify_job, sha256sum


def _create_context(tmp_path: Path):
    config = OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'catalog.db'}")
    context = create_context(config)
    reset_context(context)
    barcode = str(context.library.inventory().slots[0].barcode)
    plan, token = context.format_service.dry_run(barcode)
    assert plan.target == barcode
    context.library.load(1, 0)
    context.format_service.confirm(barcode, token.token)
    context.library.unload(0, 1)
    group = context.catalog.create_volume_group("photos")
    context.catalog.add_barcode_to_volume_group(group.id, barcode)
    return context, barcode


def test_full_archive_restore_cycle(tmp_path: Path) -> None:
    context, barcode = _create_context(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    original = source_dir / "hello.txt"
    original.write_text("hello archive")
    archive_job = context.archive_service.enqueue("photos", source_dir)
    assert archive_job.state == "completed"
    verify = run_verify_job(barcode, context.catalog, context.library, context.ltfs)
    assert verify["files_verified"] == 1
    restore_target = tmp_path / "restore"
    restore_target.mkdir()
    restore_job = context.restore_service.enqueue("/photos/hello.txt", restore_target)
    assert restore_job.state == "completed"
    restored = restore_target / "hello.txt"
    assert restored.read_text() == original.read_text()
    assert sha256sum(restored) == sha256sum(original)


def test_archive_multiple_files_then_restore_each(tmp_path: Path) -> None:
    context, _ = _create_context(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    contents = {"a.txt": "alpha", "b.txt": "beta", "c.txt": "gamma"}
    for name, content in contents.items():
        (source_dir / name).write_text(content)
    context.archive_service.enqueue("photos", source_dir)
    restore_target = tmp_path / "restore"
    restore_target.mkdir()
    for name, content in contents.items():
        context.restore_service.enqueue(f"/photos/{name}", restore_target)
        assert (restore_target / name).read_text() == content


def test_catalog_rebuild_from_tape(tmp_path: Path) -> None:
    context, barcode = _create_context(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "hello.txt").write_text("hello archive")
    context.archive_service.enqueue("photos", source_dir)
    rebuilt = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'rebuilt.db'}"))
    reset_context(rebuilt)
    rebuilt.catalog.create_volume_group("photos")
    rebuilt.catalog.add_cartridge(barcode)
    tape = context.ltfs.ensure_tape(barcode)
    for tape_path, record in tape.files.items():
        file_record = rebuilt.catalog.create_file_record(
            tape_path,
            record.size_bytes,
            record.checksum_sha256,
            rebuilt.catalog.get_volume_group("photos").id,
        )
        instance = rebuilt.catalog.create_file_instance(file_record.id, barcode, tape_path)
        rebuilt.catalog.mark_instance_archived(instance.id)
    rebuilt_records = rebuilt.catalog.list_file_records("/photos")
    assert [record.path for record in rebuilt_records] == ["/photos/hello.txt"]


def test_fuse_filesystem_lists_archived_files(tmp_path: Path) -> None:
    context, _ = _create_context(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "hello.txt").write_text("hello archive")
    context.archive_service.enqueue("photos", source_dir)
    fs = CatalogFilesystem(context.catalog, cache_dir=str(tmp_path / "cache"))
    entries = fs.listdir("/photos")
    assert [entry.name for entry in entries] == ["hello.txt"]
