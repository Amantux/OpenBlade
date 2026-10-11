"""Two overlapping archive requests against one catalog serialise on the drive lease.

The archive route's in-process lock cannot see a second process (the CLI, another
worker). The overlap here is a lease held through a *separate* catalog session on
the same SQLite file -- exactly what another process would hold -- while
``POST /archive/sharded`` runs. The request must wait for that lease, not drive
the same tape concurrently.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openblade.api.main import app
from openblade.catalog.db import get_session
from openblade.catalog.models import DriveLeaseRecord
from openblade.catalog.repository import CatalogRepository
from openblade.jobs.scheduler import CatalogLeaseStore
from tests.integration.test_sharded_archive_cli_api_parity import (
    LANES,
    build_context,
    seed_source,
)

client = TestClient(app)


def test_overlapping_archive_waits_for_a_lease_held_by_another_session(tmp_path: Path) -> None:
    context = build_context(tmp_path / "leases.db")
    context.catalog.create_volume_group("shards")
    source = tmp_path / "src"
    seed_source(source)

    other_session = get_session()
    try:
        other = CatalogLeaseStore(CatalogRepository(other_session))
        held = other.acquire(
            job_id="other-process",
            barcodes=list(LANES),
            num_drives=2,
            ttl=timedelta(minutes=15),
        )
        assert held is not None

        finished = threading.Event()
        responses: list[int] = []

        def _post() -> None:
            response = client.post(
                "/archive/sharded",
                json={
                    "source_path": str(source),
                    "volume_group": "shards",
                    "lane_barcodes": LANES,
                    "mode": "stripe",
                },
            )
            responses.append(response.status_code)
            finished.set()

        worker = threading.Thread(target=_post)
        worker.start()
        # While the other session holds both drives, the request must not finish.
        assert not finished.wait(1.0), "archive ran while another job held the drives"
        other.release([lease.id for lease in held])
        worker.join(timeout=60)
        assert finished.is_set()
        assert responses == [202]

        # The request ran under its own, strictly newer leases, now released.
        assert other.live_leases() == []
        assert max(lease.fencing_token for lease in held) < _max_token(other_session)
    finally:
        other_session.close()


def _max_token(session: Session) -> int:
    value = session.scalar(select(func.max(DriveLeaseRecord.fencing_token)))
    return int(value or 0)
