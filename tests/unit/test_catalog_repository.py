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
    record = catalog.create_file_record("/photos/a.jpg", 3, "abc", group.id)
    fetched = catalog.get_file_record("/photos/a.jpg")
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.checksum_sha256 == "abc"


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


def test_create_and_update_job() -> None:
    catalog = make_catalog()
    job = catalog.create_job("archive", {"path": "/data"})
    catalog.update_job_state(job.id, "completed")
    refreshed = catalog.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "completed"
    assert refreshed.metadata_dict == {"path": "/data"}
