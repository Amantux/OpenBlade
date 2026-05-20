from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository


def make_catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def test_create_and_get_volume_group() -> None:
    catalog = make_catalog()
    created = catalog.create_volume_group("photos")
    fetched = catalog.get_volume_group("photos")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "photos"


def test_create_file_record() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    record = catalog.create_file_record(
        "/photos/a.jpg",
        3,
        "abc",
        group.id,
        shard_count=1,
        shard_index=None,
        block_size=None,
        shard_profile="standard",
        parent_id=None,
    )
    fetched = catalog.get_file_record("/photos/a.jpg")
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.checksum_sha256 == "abc"
    assert fetched.shard_count == 1
    assert fetched.shard_index is None
    assert fetched.shard_profile == "standard"


def test_mark_instance_archived() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    record = catalog.create_file_record("/photos/a.jpg", 3, "abc", group.id)
    instance = catalog.create_file_instance(record.id, "PHO001L8", "/photos/a.jpg")
    catalog.mark_instance_archived(instance.id)
    refreshed = catalog.session.get(type(instance), instance.id)
    assert refreshed is not None
    assert refreshed.state == "archived"
    assert refreshed.checksum_verified is True


def test_list_file_records_by_prefix() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    catalog.create_file_record("/photos/a.jpg", 1, "a", group.id)
    catalog.create_file_record("/photos/2024/b.jpg", 1, "b", group.id)
    catalog.create_file_record("/docs/readme.txt", 1, "c", group.id)
    records = catalog.list_file_records("/photos")
    assert [record.path for record in records] == ["/photos/2024/b.jpg", "/photos/a.jpg"]


def test_list_ltfs_entries_filters_to_archived_instances() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    first = catalog.create_file_record("/photos/a.jpg", 3, "abc", group.id)
    archived = catalog.create_file_instance(first.id, "PHO001L8", "/photos/a.jpg")
    catalog.mark_instance_archived(archived.id)
    second = catalog.create_file_record("/photos/b.jpg", 4, "def", group.id)
    catalog.create_file_instance(second.id, "PHO002L8", "/photos/b.jpg")

    entries = catalog.list_ltfs_entries()

    assert [(entry.path, entry.tape_barcode, entry.shard_count) for entry in entries] == [
        ("/photos/a.jpg", "PHO001L8", 1)
    ]


def test_list_catalog_tape_barcodes_returns_unique_sorted_values() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    first = catalog.create_file_record("/photos/a.jpg", 3, "abc", group.id)
    second = catalog.create_file_record("/photos/b.jpg", 4, "def", group.id)
    first_instance = catalog.create_file_instance(first.id, "PHO002L8", "/photos/a.jpg")
    second_instance = catalog.create_file_instance(second.id, "PHO001L8", "/photos/b.jpg")
    duplicate_instance = catalog.create_file_instance(second.id, "PHO001L8", "/photos/b-copy.jpg")
    catalog.mark_instance_archived(first_instance.id)
    catalog.mark_instance_archived(second_instance.id)
    catalog.mark_instance_archived(duplicate_instance.id)

    assert catalog.list_catalog_tape_barcodes() == ["PHO001L8", "PHO002L8"]


def test_list_shard_records_returns_children_only() -> None:
    catalog = make_catalog()
    group = catalog.create_volume_group("photos")
    parent = catalog.create_file_record(
        "/photos/a.jpg",
        3,
        "abc",
        group.id,
        shard_count=2,
        shard_index=None,
        block_size=1024,
        shard_profile="block_stripe",
        parent_id=None,
    )
    first_shard = catalog.create_file_record(
        "/photos/a.jpg#shard0000",
        2,
        "def",
        group.id,
        shard_count=2,
        shard_index=0,
        block_size=1024,
        shard_profile="block_stripe",
        parent_id=parent.id,
    )
    second_shard = catalog.create_file_record(
        "/photos/a.jpg#shard0001",
        1,
        "ghi",
        group.id,
        shard_count=2,
        shard_index=1,
        block_size=1024,
        shard_profile="block_stripe",
        parent_id=parent.id,
    )

    listed = catalog.list_shard_records(parent.id)
    parents = catalog.list_file_records("/photos")

    assert [record.id for record in listed] == [first_shard.id, second_shard.id]
    assert [record.id for record in parents] == [parent.id]


def test_create_and_update_job() -> None:
    catalog = make_catalog()
    job = catalog.create_job("archive", {"path": "/data"})
    catalog.update_job_state(job.id, "completed")
    refreshed = catalog.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "completed"
    assert refreshed.metadata_dict == {"path": "/data"}
