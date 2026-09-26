from __future__ import annotations

from dataclasses import replace
from ipaddress import ip_address

import pytest

from openblade.web_flask import app as web_flask_app
from openblade.web_flask.app import _validate_device_url, create_app
from openblade.web_flask.client import BackendClient, BackendError
from openblade.web_flask.models import Device


class FakeBackendClient(BackendClient):
    def __init__(self) -> None:
        super().__init__(base_url="http://backend")
        self.devices = [
            Device(
                id=1,
                name="Quantum i3 A",
                emulator_url="http://device-a:8010",
                model="Scalar i3",
                role="primary",
                status="online",
                enabled=True,
                sort_order=1,
            )
        ]
        self.jobs = [
            {
                "id": "job-1",
                "state": "running",
                "job_type": "archive",
                "error": None,
                "created_at": "2026-05-30T00:00:00Z",
                "updated_at": "2026-05-30T00:01:00Z",
                "library_id": 1,
            }
        ]
        self.mounts = [
            {
                "id": "mount-1",
                "barcode": "ABC123L7",
                "drive": "DRV001",
                "state": "mounted",
                "mountTime": "2026-05-30T00:01:30Z",
            }
        ]
        self.inventory_status = {"state": "idle"}
        self.import_status = {"state": "idle"}
        self.export_status = {"state": "idle"}
        self.last_move: dict[str, object] | None = None
        self.last_export: dict[str, object] | None = None
        self.last_restore: dict[str, object] | None = None
        self.probe_calls: list[dict[str, object]] = []
        self.last_pool: dict[str, object] | None = None
        self.last_share: dict[str, object] | None = None
        self.last_magazine_action: tuple[str, str] | None = None
        self.last_policy: dict[str, object] | None = None
        self.last_cache_drive: dict[str, object] | None = None
        self.last_source_stream: dict[str, object] | None = None
        self.last_user: dict[str, object] | None = None
        self.users = [
            {"name": "admin", "role": 0, "requirePasswordChange": False},
            {"name": "operator1", "role": 1, "requirePasswordChange": False},
        ]
        self.policies = [
            {
                "id": "balanced",
                "name": "Balanced",
                "policy_type": "balanced",
                "default_ingest_mode": "cache_drive",
                "copies_required": 1,
                "allow_sharding": False,
                "shard_size_bytes": None,
                "max_parallelism": 1,
                "auto_clean_before_archive": True,
            }
        ]
        self.cache_drives = [
            {
                "id": "cache-primary",
                "name": "Primary Cache",
                "root_path": "/openblade/cache",
                "max_bytes": 107374182400,
                "min_free_bytes": 5368709120,
                "eviction_policy": "after_verified",
                "enabled": True,
            }
        ]
        self.source_stream = {
            "enabled": True,
            "require_source_online_for_entire_job": True,
            "preflight_read_check": True,
            "checksum_mode": "precompute_and_post_verify",
            "max_retries": 3,
            "fail_on_source_change": True,
            "allow_partial_dataset_success": False,
        }
        self.volume_groups = [{"id": "vg-default", "name": "Default", "barcodes": ["ABC123L7"]}]
        self.pools = [
            {
                "id": "pool-1",
                "name": "Primary Pool",
                "replication_factor": 1,
                "backup_order_mode": "sequential",
                "access_mode": "read_only",
                "volume_group_ids": ["vg-default"],
            }
        ]
        self.shares = [
            {
                "path": "/openblade/inbox",
                "name": "inbox",
                "share_type": "inbox",
                "pool_ids": ["pool-1"],
                "folder_mappings": [
                    {"folder_path": "/inbox", "pool_id": "pool-1", "access_mode": "read_write"}
                ],
                "writable": True,
            }
        ]
        self.magazines = [
            {
                "id": "MAG-1",
                "location": "left",
                "status": "inserted",
                "slotCount": 10,
                "occupiedSlots": 2,
                "tapes": ["ABC123L7"],
            },
        ]
        self.magazine_slots = {
            "MAG-1": [
                {
                    "id": "slot-1",
                    "address": "1,1,1",
                    "state": "occupied",
                    "barcode": "ABC123L7",
                    "type": "magazine",
                }
            ]
        }
        self.datasets = [
            {
                "id": "dataset-1",
                "name": "finance-q1",
                "pool_id": "pool-1",
                "source_path": "/openblade/inbox/finance-q1",
                "file_count": 2,
                "total_bytes": 4096,
                "status": "archived",
            }
        ]
        self.last_dataset_verify_id: str | None = None
        self.last_archive: dict[str, object] | None = None
        #: AML role the fake reports from /aml/users/me. 0 == Administrator, which
        #: is what most tests need; set it to 1 or 2 to exercise the write gates.
        self.current_role = 0
        self.logout_calls: list[str] = []

    def login(self, *, username: str, password: str) -> str:
        if username == "admin" and password == "admin":
            return "session-token"
        raise BackendError(status_code=401, detail="invalid credentials")

    def get_current_user(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {
            "name": "admin",
            "role": self.current_role,
            "requirePasswordChange": False,
        }

    def logout(self, token: str) -> None:
        self.logout_calls.append(token)

    def list_devices(self, token: str) -> list[Device]:
        assert token == "session-token"
        return self.devices

    def get_device(self, token: str, device_id: int) -> Device | None:
        assert token == "session-token"
        for device in self.devices:
            if device.id == device_id:
                return device
        return None

    def create_device(self, token: str, payload: dict[str, object]) -> Device:
        assert token == "session-token"
        next_device = replace(
            self.devices[0],
            id=2,
            name=str(payload["name"]),
            emulator_url=str(payload["emulator_url"]),
            model=str(payload["model"]),
            role=str(payload["role"]),
            sort_order=int(payload["sort_order"]),
        )
        self.devices.append(next_device)
        return next_device

    def probe_device_endpoint(
        self,
        *,
        connection_url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, object]:
        self.probe_calls.append(
            {"connection_url": connection_url, "username": username, "password": password}
        )
        if "invalid" in connection_url:
            raise BackendError(status_code=400, detail="Unable to reach device health endpoint")
        return {"reachable": True, "authenticated": bool(username)}

    def update_device(self, token: str, device_id: int, payload: dict[str, object]) -> Device:
        assert token == "session-token"
        for index, device in enumerate(self.devices):
            if device.id == device_id:
                updated = replace(
                    device,
                    name=str(payload["name"]),
                    emulator_url=str(payload["emulator_url"]),
                    role=str(payload["role"]),
                    enabled=bool(payload["enabled"]),
                )
                self.devices[index] = updated
                return updated
        raise AssertionError("device not found")

    def list_jobs(
        self,
        token: str,
        *,
        library_id: int | None = None,
        state: str | None = None,
        job_type: str | None = None,
    ) -> list[dict[str, object]]:
        assert token == "session-token"
        jobs: list[dict[str, object]] = self.jobs
        if library_id is not None:
            jobs = [item for item in jobs if int(item["library_id"]) == library_id]
        if state is not None:
            jobs = [item for item in jobs if str(item["state"]) == state]
        if job_type is not None:
            jobs = [item for item in jobs if str(item["job_type"]) == job_type]
        return jobs

    def list_media(
        self, token: str, *, library_id: int, limit: int = 100
    ) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return [
            {
                "barcode": "ABC123L7",
                "type": "LTO7",
                "state": "online",
                "partition": "p1",
                "slot": "1,1,1",
            }
        ]

    def get_file_versions(self, token: str, *, file_path: str) -> list[dict[str, object]]:
        assert token == "session-token"
        assert file_path
        return [
            {
                "file_path": file_path,
                "tape_barcode": "ABC123L7",
                "tape_slot": "1,1,1",
                "checksum": "abc123",
            }
        ]

    def list_drives(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return [
            {"serialNumber": "DRV001", "type": "LTO7", "state": "online", "loadedMedia": "ABC123L7"}
        ]

    def list_partitions(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return [{"name": "p1", "type": "classic", "state": "online", "slotCount": 24}]

    def trigger_inventory(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return {"message": "Inventory started"}

    def move_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        source: str,
        destination: str,
        barcode: str | None = None,
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        self.last_move = {"source": source, "destination": destination, "barcode": barcode}
        return {"resultCode": "0", "resultMsg": "Move queued"}

    def mount_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcode: str,
        drive: str,
        partition: str | None = None,
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        assert barcode
        assert drive
        return {"resultCode": "0", "resultMsg": "Mount queued"}

    def unmount_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcode: str,
        drive: str,
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        assert barcode
        assert drive
        return {"resultCode": "0", "resultMsg": "Unmount queued"}

    def start_import_operation(
        self,
        token: str,
        *,
        library_id: int,
        partition: str,
        ie_station: str,
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        assert partition
        assert ie_station
        return {"resultCode": "0", "resultMsg": "Import queued"}

    def start_export_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcodes: list[str],
        ie_station: str,
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        assert barcodes
        assert ie_station
        self.last_export = {"barcodes": barcodes, "ie_station": ie_station}
        return {"resultCode": "0", "resultMsg": "Export queued"}

    def list_mounts(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return self.mounts

    def list_ie_stations(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return [{"id": "IE-1", "status": "closed"}]

    def get_inventory_status(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return self.inventory_status

    def get_import_status(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return self.import_status

    def get_export_status(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return self.export_status

    def list_media_pools(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return [{"name": "default", "type": "archive"}]

    def list_nas_shares(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.shares

    def list_nas_pools(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.pools

    def list_aml_users(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.users

    def create_aml_user(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        assert token == "session-token"
        self.last_user = payload
        record = {
            "name": str(payload["name"]),
            "role": int(payload["role"]),
            "requirePasswordChange": False,
        }
        self.users.append(record)
        return record

    def list_nas_policies(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.policies

    def create_or_update_nas_policy(
        self, token: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert token == "session-token"
        self.last_policy = payload
        self.policies = [payload]
        return payload

    def list_cache_drives(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.cache_drives

    def create_or_update_cache_drive(
        self, token: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert token == "session-token"
        self.last_cache_drive = payload
        self.cache_drives = [payload]
        return payload

    def get_source_stream_config(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return self.source_stream

    def update_source_stream_config(
        self, token: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert token == "session-token"
        self.last_source_stream = payload
        self.source_stream = payload
        return payload

    def list_volume_groups(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return self.volume_groups

    def create_nas_pool(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        assert token == "session-token"
        self.last_pool = payload
        self.pools = [payload]
        return payload

    def create_nas_share(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        assert token == "session-token"
        self.last_share = payload
        self.shares.append(payload)
        return payload

    def list_magazines(self, token: str, *, library_id: int) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return self.magazines

    def list_magazine_slots(
        self, token: str, *, library_id: int, magazine_id: str
    ) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        return self.magazine_slots.get(magazine_id, [])

    def eject_magazine(self, token: str, *, library_id: int, magazine_id: str) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        self.last_magazine_action = ("eject", magazine_id)
        return {"resultCode": "0"}

    def insert_magazine(
        self, token: str, *, library_id: int, magazine_id: str
    ) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        self.last_magazine_action = ("insert", magazine_id)
        return {"resultCode": "0"}

    def get_gateway_config(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {"bind_host": "0.0.0.0", "bind_port": 2222, "status": "running"}

    def get_gateway_status(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {"status": "running", "active_sessions": 1, "total_sessions": 3}

    def list_gateway_credentials(self, token: str) -> list[dict[str, object]]:
        assert token == "session-token"
        return [{"username": "ops-user", "enabled": True}]

    def get_event_summary(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return {"total": 3, "critical": 1, "warning": 1, "info": 1}

    def get_alert_summary(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return {"critical": 1, "warning": 0, "info": 1}

    def list_events(
        self,
        token: str,
        *,
        library_id: int,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        assert token == "session-token"
        assert library_id == 1
        events = [
            {
                "timestamp": "2026-05-30T00:00:00Z",
                "severity": "critical",
                "component": "drive",
                "message": "Drive alert",
            }
        ]
        if severity and severity != "all":
            return [item for item in events if item["severity"] == severity]
        return events[:limit]

    def get_system_status(self, token: str, *, library_id: int | None = None) -> dict[str, object]:
        assert token == "session-token"
        return {
            "overall": "good",
            "cpu": "good",
            "memory": "warning",
            "disk": "good",
            "network": "good",
            "services": "good",
        }

    def get_health(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {"status": "healthy"}

    def get_system_config(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {
            "version": "0.1.0",
            "backend": "simulator",
            "library_count": 1,
            "gateway_enabled": True,
        }

    def get_catalog_status(self, token: str) -> dict[str, object]:
        assert token == "session-token"
        return {
            "db_reachable": True,
            "total_file_records": 12,
            "total_datasets": 2,
            "total_cartridges": 4,
            "total_path_mappings": 12,
            "last_rebuild_status": "completed",
        }

    def get_library_status(self, token: str, *, library_id: int) -> dict[str, object]:
        assert token == "session-token"
        assert library_id == 1
        return {"overall": "good"}

    def list_catalog_files(self, token: str, *, limit: int = 25) -> dict[str, object]:
        assert token == "session-token"
        return {
            "files": [
                {
                    "path": "/vg/file1",
                    "size_bytes": 10,
                    "primary_barcode": "ABC123L7",
                    "shard_count": 1,
                }
            ],
            "total": 1,
        }

    def list_nas_datasets(
        self,
        token: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        assert token == "session-token"
        datasets = self.datasets[:limit]
        if status is not None:
            datasets = [item for item in datasets if str(item.get("status")) == status]
        return datasets

    def verify_nas_dataset(self, token: str, *, dataset_id: str) -> dict[str, object]:
        assert token == "session-token"
        self.last_dataset_verify_id = dataset_id
        for dataset in self.datasets:
            if str(dataset.get("id")) == dataset_id:
                dataset["status"] = "verified"
        return {
            "dataset_id": dataset_id,
            "files_verified": 2,
            "files_corrupt": 0,
            "files_updated": 1,
            "checksums": {"sample.bin": "abc123"},
        }

    def create_archive_job(
        self, token: str, *, source_path: str, volume_group: str
    ) -> dict[str, object]:
        assert token == "session-token"
        assert source_path
        assert volume_group
        self.last_archive = {"source_path": source_path, "volume_group": volume_group}
        return {"job_id": "job-archive", "status": "pending"}

    def create_restore_job(
        self, token: str, *, catalog_path: str, dest_path: str
    ) -> dict[str, object]:
        assert token == "session-token"
        assert catalog_path
        assert dest_path
        self.last_restore = {"catalog_path": catalog_path, "destination_path": dest_path}
        return {"job_id": "job-restore", "status": "pending"}


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    # _validate_device_url resolves device hostnames and fails closed on names that
    # do not resolve. The fixture's device names ("device-b") are fictional, so stub
    # the resolver to a public address rather than making the suite depend on live
    # DNS. The deny-list behaviour itself is covered directly against
    # _validate_device_url in the SSRF tests below.
    monkeypatch.setattr(
        web_flask_app, "_resolve_device_host", lambda hostname: [ip_address("198.51.100.10")]
    )
    app = create_app(client_factory=FakeBackendClient)
    app.config["TESTING"] = True
    return app.test_client()


def _backend_from_client(client) -> FakeBackendClient:
    app = client.application
    backend = app.extensions["backend_client"]
    assert isinstance(backend, FakeBackendClient)
    return backend


def _csrf(client) -> str:
    with client.session_transaction() as sess:
        return str(sess["_csrf_token"])


def _login(client) -> None:
    client.get("/login")
    client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )


def test_requires_auth_for_devices_page(app_client) -> None:
    response = app_client.get("/devices")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_and_devices_page(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Connected Devices" in body
    assert "Fleet" not in body
    assert "Manage" in body
    assert "Reports" in body
    assert "System" in body


def test_login_rejects_protocol_relative_next_redirect(app_client) -> None:
    app_client.get("/login?next=//evil.example/path")
    response = app_client.post(
        "/login",
        data={
            "username": "admin",
            "password": "admin",
            "next": "//evil.example/path",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/devices")


def test_login_rejects_control_chars_in_next_redirect(app_client) -> None:
    app_client.get("/login")
    response = app_client.post(
        "/login",
        data={
            "username": "admin",
            "password": "admin",
            "next": "/devices\x00//evil.example",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/devices")


def test_login_session_is_permanent(app_client) -> None:
    _login(app_client)
    with app_client.session_transaction() as sess:
        assert sess.permanent is True


def test_logout_requires_post_and_csrf(app_client) -> None:
    _login(app_client)
    get_response = app_client.get("/logout")
    assert get_response.status_code == 405
    post_response = app_client.post(
        "/logout", data={"csrf_token": _csrf(app_client)}, follow_redirects=False
    )
    assert post_response.status_code == 302
    assert post_response.headers["Location"].endswith("/login")


def test_login_rate_limit_blocks_after_repeated_failures(app_client) -> None:
    app_client.get("/login")
    for _ in range(8):
        response = app_client.post(
            "/login",
            data={"username": "admin", "password": "bad-password", "csrf_token": _csrf(app_client)},
            follow_redirects=False,
        )
        assert response.status_code == 401
    blocked = app_client.post(
        "/login",
        data={"username": "admin", "password": "bad-password", "csrf_token": _csrf(app_client)},
        follow_redirects=False,
    )
    assert blocked.status_code == 429


def test_login_rate_limit_ignores_forged_forwarded_for(app_client) -> None:
    """A rotating ``X-Forwarded-For`` must not mint fresh rate-limit identities.

    The header is caller-controlled, so if ``_client_ip`` honoured it an attacker
    would defeat the login lockout entirely by sending a new value each attempt.
    Mirrors the rule documented on ``openblade.api.routes_assist.client_key``.

    Mutation check: restore the ``X-Forwarded-For`` branch in ``_client_ip`` and
    this test fails — every attempt lands in a distinct bucket, so the ninth POST
    returns 401 instead of 429.
    """
    app_client.get("/login")
    for attempt in range(8):
        response = app_client.post(
            "/login",
            data={
                "username": "admin",
                "password": "bad-password",
                "csrf_token": _csrf(app_client),
            },
            headers={"X-Forwarded-For": f"10.9.9.{attempt}"},
            follow_redirects=False,
        )
        assert response.status_code == 401
    blocked = app_client.post(
        "/login",
        data={"username": "admin", "password": "bad-password", "csrf_token": _csrf(app_client)},
        headers={"X-Forwarded-For": "10.9.9.200"},
        follow_redirects=False,
    )
    assert blocked.status_code == 429


def test_register_device_requires_csrf(app_client) -> None:
    _login(app_client)
    response = app_client.post(
        "/devices/register",
        data={
            "name": "Quantum i3 B",
            "connection_url": "http://device-b:8010",
            "serial_number": "SN-2",
            "model": "Scalar i3",
        },
    )
    assert response.status_code == 400


def test_register_device_flow(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/register")
    response = app_client.post(
        "/devices/register",
        data={
            "name": "Quantum i3 B",
            "connection_url": "http://device-b:8010",
            "serial_number": "SN-2",
            "model": "Scalar i3",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/devices")
    backend = _backend_from_client(app_client)
    assert backend.probe_calls[-1]["connection_url"] == "http://device-b:8010"


def test_register_page_hides_storage_role_and_supports_probe_auth(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices/register")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Storage role" not in body
    assert "Device Username (optional)" in body
    assert "Device Password (optional)" in body


def test_devices_index_hides_role_metadata(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Role:" not in body


def test_probe_endpoint_validates_auth_connectivity(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/register")
    response = app_client.post(
        "/devices/probe",
        data={
            "connection_url": "http://device-b:8010",
            "device_username": "operator",
            "device_password": "secret",
            "csrf_token": _csrf(app_client),
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload is not None
    assert payload["ok"] is True
    assert payload["probe"]["reachable"] is True
    assert payload["probe"]["authenticated"] is True
    backend = _backend_from_client(app_client)
    assert backend.probe_calls[-1]["username"] == "operator"


def test_manage_page_is_device_scoped(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices/1/manage", follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Inventory" in body
    assert "Drives" in body
    assert "Quantum i3 A" in body


def test_jobs_reports_and_system_pages_render(app_client) -> None:
    _login(app_client)
    jobs_response = app_client.get("/jobs")
    reports_response = app_client.get("/reports")
    system_response = app_client.get("/system")
    assert jobs_response.status_code == 200
    assert reports_response.status_code == 200
    assert system_response.status_code == 200
    assert "Jobs" in jobs_response.get_data(as_text=True)
    assert "Reports" in reports_response.get_data(as_text=True)
    assert "System Health" in system_response.get_data(as_text=True)


def test_system_page_lists_users_and_creation_controls(app_client) -> None:
    _login(app_client)
    response = app_client.get("/system")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Multi-user accounts" in body
    assert "admin" in body
    assert "Create user" in body


def test_system_create_user_flow(app_client) -> None:
    _login(app_client)
    app_client.get("/system")
    response = app_client.post(
        "/system/users",
        data={
            "username": "operator2",
            "password": "operator-password",
            "role": "1",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/system")
    backend = _backend_from_client(app_client)
    assert backend.last_user is not None
    assert backend.last_user["name"] == "operator2"
    assert backend.last_user["role"] == 1


def test_storage_paths_reject_parent_traversal(app_client) -> None:
    _login(app_client)
    app_client.get("/storage/archive")
    response = app_client.post(
        "/storage/archive",
        data={
            "source_path": "/data/../etc",
            "volume_group": "default",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/storage/archive")


def test_storage_access_page_shows_windows_and_linux_mount_commands(app_client) -> None:
    _login(app_client)
    response = app_client.get("/storage/access")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Windows mapped drive" in body
    assert "net use Z:" in body
    assert "mount -t cifs" in body
    assert "sftp -P 2222 ops-user@" in body


def test_storage_write_path_page_renders_controls(app_client) -> None:
    _login(app_client)
    response = app_client.get("/storage/write-path")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Policy for multi-tape writes" in body
    assert "Cache drive staging" in body
    assert "Streaming direct-to-tape" in body
    assert "Folder-to-pool access mapping" in body
    assert "Optimization guidance" in body


def test_storage_catalog_page_renders_dataset_verification_workbench(app_client) -> None:
    _login(app_client)
    response = app_client.get("/storage/catalog")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Dataset verification workbench" in body
    assert "finance-q1" in body
    assert "Verify now" in body


def test_storage_catalog_verify_dataset_flow(app_client) -> None:
    _login(app_client)
    app_client.get("/storage/catalog")
    response = app_client.post(
        "/storage/datasets/dataset-1/verify",
        data={"csrf_token": _csrf(app_client)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/storage/catalog")
    backend = _backend_from_client(app_client)
    assert backend.last_dataset_verify_id == "dataset-1"

    catalog = app_client.get("/storage/catalog")
    body = catalog.get_data(as_text=True)
    assert "Last verify run" in body
    assert "dataset-1" in body


def test_storage_write_path_actions_flow(app_client) -> None:
    _login(app_client)
    app_client.get("/storage/write-path")
    policy_response = app_client.post(
        "/storage/policies",
        data={
            "policy_id": "critical-fast",
            "name": "Critical Fast",
            "policy_type": "critical_sequential",
            "default_ingest_mode": "source_stream",
            "copies_required": "2",
            "max_parallelism": "4",
            "shard_size_bytes": "1048576",
            "shard_strategy": "round_robin",
            "allow_sharding": "on",
            "verify_before_archive": "on",
            "verify_after_archive": "on",
            "auto_clean_before_archive": "on",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert policy_response.status_code == 302
    backend = _backend_from_client(app_client)
    assert backend.last_policy is not None
    assert backend.last_policy["id"] == "critical-fast"
    assert backend.last_policy["copies_required"] == 2
    assert backend.last_policy["shard_size_bytes"] == 1048576
    assert backend.last_policy["auto_clean_before_archive"] is True

    app_client.get("/storage/write-path")
    cache_response = app_client.post(
        "/storage/cache-drives",
        data={
            "drive_id": "cache-b",
            "name": "Cache B",
            "root_path": "/openblade/cache/b",
            "max_bytes": "1000000",
            "min_free_bytes": "1000",
            "retention_days": "7",
            "stabilization_seconds": "3",
            "eviction_policy": "after_verified",
            "enabled": "on",
            "verify_before_archive": "on",
            "verify_after_archive": "on",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert cache_response.status_code == 302
    assert backend.last_cache_drive is not None
    assert backend.last_cache_drive["id"] == "cache-b"
    assert backend.last_cache_drive["root_path"] == "/openblade/cache/b"

    app_client.get("/storage/write-path")
    source_response = app_client.post(
        "/storage/source-stream",
        data={
            "checksum_mode": "streaming",
            "max_retries": "2",
            "enabled": "on",
            "require_source_online_for_entire_job": "on",
            "preflight_read_check": "on",
            "fail_on_source_change": "on",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert source_response.status_code == 302
    assert backend.last_source_stream is not None
    assert backend.last_source_stream["checksum_mode"] == "streaming"
    assert backend.last_source_stream["max_retries"] == 2

    app_client.get("/storage/write-path")
    share_response = app_client.post(
        "/storage/shares",
        data={
            "share_path": "/openblade/finance",
            "share_name": "finance",
            "share_type": "pool",
            "default_policy_id": "critical-fast",
            "pool_ids": "pool-1,pool-2",
            "folder_mappings": "/finance/reports|pool-1|read_only\n/finance/ops|pool-2|read_write",
            "writable": "on",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert share_response.status_code == 302
    assert backend.last_share is not None
    assert backend.last_share["path"] == "/openblade/finance"
    assert backend.last_share["pool_ids"] == ["pool-1", "pool-2"]
    assert len(backend.last_share["folder_mappings"]) == 2


def test_device_operations_actions_flow(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/1/manage/operations")
    response = app_client.post(
        "/devices/1/operations/move",
        data={
            "source": "1,1,1",
            "destination": "DRV-1",
            "barcode": "ABC123L7",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/devices/1/manage/operations")
    backend = _backend_from_client(app_client)
    assert backend.last_move == {"source": "1,1,1", "destination": "DRV-1", "barcode": "ABC123L7"}


def test_operations_section_includes_pool_magazine_and_search_ui(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices/1/manage/operations")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Pool Controls" in body
    assert "Tape and Content Search" in body
    assert "Magazine Control" in body


def test_create_pool_and_map_share_actions(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/1/manage/operations")
    pool_response = app_client.post(
        "/devices/1/operations/pool",
        data={
            "pool_id": "pool-2",
            "pool_name": "Primary Pool",
            "volume_group_ids": "vg-default",
            "restore_target_path": "/openblade/restore",
            "replication_factor": "2",
            "backup_order_mode": "sequential",
            "access_mode": "read_write",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert pool_response.status_code == 302
    backend = _backend_from_client(app_client)
    assert backend.last_pool is not None
    assert backend.last_pool["name"] == "Primary Pool"
    assert backend.last_pool["replication_factor"] == 2

    app_client.get("/devices/1/manage/operations")
    share_response = app_client.post(
        "/devices/1/operations/share",
        data={
            "share_name": "poolshare",
            "pool_id": "pool-1",
            "writable": "on",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert share_response.status_code == 302
    assert backend.last_share is not None
    assert backend.last_share["path"] == "/pools/pool-1"
    assert backend.last_share["share_type"] == "pool"


def test_hydrate_restore_action_uses_latest_tape_copy(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/1/manage/operations")
    response = app_client.post(
        "/devices/1/operations/restore",
        data={
            "catalog_path": "/archive/file-a.bin",
            # /openblade/restore, not a bare /restore: dest_path is a real
            # filesystem write in jobs/restore.py, so it must sit under an allowed
            # storage root. This matches tests/i3/test_09_restore_cycle.py.
            "dest_path": "/openblade/restore",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    backend = _backend_from_client(app_client)
    assert backend.last_restore is not None
    assert backend.last_restore["destination_path"] == "/openblade/restore"


def test_magazine_eject_action_calls_backend(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/1/manage/operations")
    response = app_client.post(
        "/devices/1/operations/magazine/MAG-1/eject",
        data={"csrf_token": _csrf(app_client)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    backend = _backend_from_client(app_client)
    assert backend.last_magazine_action == ("eject", "MAG-1")


def test_export_operation_validates_barcodes(app_client) -> None:
    _login(app_client)
    app_client.get("/devices/1/manage/operations")
    response = app_client.post(
        "/devices/1/operations/export",
        data={
            "barcodes": "GOOD123 BAD*CODE",
            "ie_station": "IE-1",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    backend = _backend_from_client(app_client)
    assert backend.last_export is None


def test_jobs_sse_stream_returns_snapshot(app_client) -> None:
    _login(app_client)
    response = app_client.get("/events/jobs?once=1")
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")
    body = response.get_data(as_text=True)
    assert "event: jobs.snapshot" in body
    assert '"running": 1' in body


def test_devices_sse_stream_returns_snapshot(app_client) -> None:
    _login(app_client)
    response = app_client.get("/events/devices?once=1")
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")
    body = response.get_data(as_text=True)
    assert "event: devices.snapshot" in body
    assert "Quantum i3 A" in body


def test_security_headers_present(app_client) -> None:
    _login(app_client)
    response = app_client.get("/devices")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_headers_send_native_api_token_as_bearer_and_session_as_cookie() -> None:
    """With native auth on, Authorization must carry the NATIVE token, not the session.

    openblade.api.api_auth gates the native surface on the static
    OPENBLADE_API_TOKEN, while /aml/* authenticates per-user via
    routes_aml_auth.require_auth, which checks the ``sessionID`` cookie before the
    bearer. Sending the AML session token as the only bearer 401s every native
    endpoint (verified against the live app: /jobs, /nas/policies, /api/libraries
    and /volume-groups all returned 401).

    Mutation check: drop ``self.api_token or`` from BackendClient._headers and the
    bearer assertion below fails.
    """
    client = BackendClient(base_url="http://backend", api_token="native-tok")
    headers = client._headers("aml-session-token")
    assert headers["Authorization"] == "Bearer native-tok"
    assert headers["Cookie"] == "sessionID=aml-session-token"


def test_headers_fall_back_to_session_bearer_when_no_native_token() -> None:
    """No OPENBLADE_API_TOKEN configured => unchanged pre-existing behaviour.

    Native auth is disabled in that case, so the session token stays in
    Authorization and nothing about the previous wiring changes.
    """
    client = BackendClient(base_url="http://backend")
    headers = client._headers("aml-session-token")
    assert headers["Authorization"] == "Bearer aml-session-token"
    assert headers["Cookie"] == "sessionID=aml-session-token"


@pytest.mark.parametrize(
    "url",
    [
        # Loopback, spelled every way that defeats a string deny-list. Each of the
        # first three was PROVEN to reach a live listener on 127.0.0.1 before the
        # resolve-and-check fix.
        "http://127.0.0.1:9911",
        "http://127.1:9911",
        "http://2130706433:9911",
        "http://localhost.:9911",
        "http://[::1]:9911",
        "http://0/",
        # Cloud metadata, by name and as a packed integer.
        "http://169.254.169.254/",
        "http://2852039166/",
        "http://metadata.google.internal/",
        # Other non-routable classes.
        "http://224.0.0.1/",
        "http://240.0.0.1/",
    ],
)
def test_validate_device_url_blocks_ssrf_targets(url: str) -> None:
    """A device URL must never be pointed at loopback, metadata, or multicast.

    Mutation check: delete the `_is_unsafe_ip` call from `_validate_device_url` (or
    drop `is_loopback`/`is_link_local` from `_is_unsafe_ip`) and these fail. The
    previous implementation compared HOST SPELLINGS against a set, which is why
    "127.1" and "2130706433" both sailed through it.
    """
    assert _validate_device_url(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        # RFC1918 stays allowed ON PURPOSE: a real Scalar i3 lives on the operator's
        # LAN, so blocking private space would refuse every legitimate device.
        "http://10.0.0.5:8010/",
        "http://192.168.1.10:8010/",
        "http://172.16.0.1:8010/",
    ],
)
def test_validate_device_url_allows_lan_devices(url: str) -> None:
    assert _validate_device_url(url) is None


def test_validate_device_url_rejects_unresolvable_host() -> None:
    """Fail closed: an unresolvable name is refused, not probed."""
    assert _validate_device_url("http://no-such-host.invalid:8010/") is not None


def test_validate_device_url_checks_resolved_address_not_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A benign-looking name that RESOLVES to loopback must be refused.

    This is the case a deny-list can never catch, because the attacker owns the DNS
    record. Mutation check: make _validate_device_url skip the resolved-address loop
    and this fails.
    """
    monkeypatch.setattr(
        web_flask_app, "_resolve_device_host", lambda hostname: [ip_address("127.0.0.1")]
    )
    assert _validate_device_url("http://totally-normal.example.com:8010/") is not None


def test_validate_device_url_rejects_embedded_credentials() -> None:
    assert _validate_device_url("http://user:pw@10.0.0.5:8010/") is not None


def test_validate_device_url_override_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented development escape hatch still works."""
    monkeypatch.setenv("OPENBLADE_WEB_ALLOW_UNSAFE_DEVICE_TARGETS", "true")
    assert _validate_device_url("http://127.0.0.1:8010/") is None


def test_probe_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must not chase a redirect to an unvetted host.

    The vetted URL is only the FIRST hop; a redirect is a second destination nobody
    checked, and httpx replays the POST body on 307/308, which would hand the
    submitted device password to whatever host the redirect names.

    Mutation check: set follow_redirects=True in probe_device_endpoint and this test
    fails, because the 302 would be chased to a 200.
    """
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1:9913/healthz"}

    class FakeHttpxClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> FakeHttpxClient:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

        def post(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    import openblade.web_flask.client as client_module

    monkeypatch.setattr(client_module.httpx, "Client", FakeHttpxClient)
    client = BackendClient(base_url="http://backend")
    with pytest.raises(BackendError):
        client.probe_device_endpoint(connection_url="http://10.0.0.5:8010")
    assert captured.get("follow_redirects") is False


@pytest.mark.parametrize(
    ("path", "field", "payload", "recorder"),
    [
        ("/storage/archive", "source_path", {"volume_group": "vg-default"}, "last_archive"),
        ("/storage/restore", "dest_path", {"catalog_path": "/archive/f.bin"}, "last_restore"),
        (
            "/storage/cache-drives",
            "root_path",
            {"drive_id": "c1", "name": "C"},
            "last_cache_drive",
        ),
    ],
)
def test_storage_writes_reject_paths_outside_allowed_roots(
    app_client, path: str, field: str, payload: dict[str, str], recorder: str
) -> None:
    """Operator paths are confined to allowed roots, not merely traversal-checked.

    These values reach real filesystem sinks in the API container: source_path is
    rglob'd by routes_archive, dest_path is written by jobs/restore, root_path
    becomes a cache-drive root. Those backend routes have no per-user authorization,
    so /etc, /root and /proc/self/environ -- which holds OPENBLADE_API_TOKEN -- must
    be unreachable from this form even though none of them contains "..".

    Mutation check: swap _is_allowed_storage_root back to _is_safe_storage_path at
    the call site and these fail, because "/etc/passwd" passes a traversal check.
    """
    _login(app_client)
    backend = _backend_from_client(app_client)
    app_client.get("/storage/overview")
    for bad in ("/etc/passwd", "/root/.ssh", "/proc/self/environ", "/"):
        data = {**payload, field: bad, "csrf_token": _csrf(app_client)}
        app_client.post(path, data=data, follow_redirects=False)
        assert getattr(backend, recorder) is None, f"{bad} reached the backend via {field}"


def test_archive_accepts_path_under_allowed_root(app_client) -> None:
    """The confinement must not block the legitimate case."""
    _login(app_client)
    app_client.get("/storage/overview")
    response = app_client.post(
        "/storage/archive",
        data={
            "source_path": "/openblade/inbox/finance",
            "volume_group": "vg-default",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/jobs")


def test_archive_rejects_unvalidated_volume_group(app_client) -> None:
    """volume_group was the one field reaching the backend with no allowlist.

    It becomes a catalog path component via PurePosixPath("/") / volume_group, and
    PurePosixPath does not normalise, so ".." would persist into catalog keys.
    """
    _login(app_client)
    app_client.get("/storage/overview")
    response = app_client.post(
        "/storage/archive",
        data={
            "source_path": "/openblade/inbox/finance",
            "volume_group": "../../etc",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/storage/archive")


@pytest.mark.parametrize("role", [1, 2])
def test_config_writes_require_admin_role(app_client, role: int) -> None:
    """Non-admin sessions cannot rewrite storage configuration.

    This gate lives in the UI because the backend's config surface has none:
    /nas/policies, /nas/shares, /nas/pools, /nas/cache-drives and
    /nas/source-stream carry no require_auth. Since BackendClient presents the
    instance-wide OPENBLADE_API_TOKEN as the native bearer, every session would
    otherwise act with full native authority regardless of its AML role.

    Mutation check: remove the _require_admin() calls and these fail -- role 1 and 2
    reach the backend and last_policy/last_cache_drive get set.
    """
    backend = _backend_from_client(app_client)
    backend.current_role = role
    _login(app_client)
    app_client.get("/storage/overview")
    for path, data in (
        ("/storage/policies", {"policy_id": "p1", "name": "P", "policy_type": "balanced"}),
        ("/storage/cache-drives", {"drive_id": "c1", "name": "C", "root_path": "/openblade/cache"}),
        ("/storage/source-stream", {"checksum_mode": "streaming", "max_retries": "2"}),
        (
            "/storage/shares",
            {"share_path": "/openblade/x", "share_name": "x", "share_type": "pool"},
        ),
    ):
        response = app_client.post(
            path, data={**data, "csrf_token": _csrf(app_client)}, follow_redirects=False
        )
        assert response.status_code == 302
    assert backend.last_policy is None
    assert backend.last_cache_drive is None
    assert backend.last_source_stream is None
    assert backend.last_share is None


def test_config_writes_allowed_for_admin_role(app_client) -> None:
    """The admin path still works -- the gate is not a blanket refusal."""
    _login(app_client)
    backend = _backend_from_client(app_client)
    app_client.get("/storage/write-path")
    response = app_client.post(
        "/storage/policies",
        data={
            "policy_id": "critical-fast",
            "name": "Critical Fast",
            "policy_type": "critical_sequential",
            "copies_required": "2",
            "max_parallelism": "1",
            "csrf_token": _csrf(app_client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert backend.last_policy is not None


def test_unknown_role_fails_closed(app_client) -> None:
    """If /aml/users/me cannot be read, writes are denied rather than allowed.

    A role we could not determine must never default to Administrator.
    """
    backend = _backend_from_client(app_client)

    def boom(token: str) -> dict[str, object]:
        raise BackendError(status_code=503, detail="unavailable")

    backend.get_current_user = boom  # type: ignore[method-assign]
    _login(app_client)
    app_client.get("/storage/overview")
    response = app_client.post(
        "/storage/policies",
        data={"policy_id": "p1", "name": "P", "csrf_token": _csrf(app_client)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert backend.last_policy is None


def test_logout_revokes_the_backend_session(app_client) -> None:
    """Clearing the Flask cookie is not enough; the AML sessionID must be revoked."""
    _login(app_client)
    backend = _backend_from_client(app_client)
    app_client.post("/logout", data={"csrf_token": _csrf(app_client)}, follow_redirects=False)
    assert backend.logout_calls == ["session-token"]


@pytest.mark.parametrize("value", ["..", ".", "..."])
def test_identifier_allowlist_rejects_dot_only_segments(app_client, value: str) -> None:
    """Dot-only identifiers are refused before they reach a URL or a path.

    _POOL_ID_ALLOWED permits ".", so ".." matched the alphabet. These values are
    interpolated into backend URLs and into /pools/<pool_id>, and both httpx and
    POSIX normalisation collapse a ".." segment -- verified: POST
    /storage/datasets/../verify rewrote the backend URL to /nas/verify.

    Mutation check: call pattern.fullmatch directly instead of _is_safe_identifier
    and these fail.
    """
    _login(app_client)
    backend = _backend_from_client(app_client)
    app_client.get("/storage/catalog")
    app_client.post(
        f"/storage/datasets/{value}/verify",
        data={"csrf_token": _csrf(app_client)},
        follow_redirects=False,
    )
    assert backend.last_dataset_verify_id is None


def test_redirect_target_rejects_backslash() -> None:
    """ "/\\evil.example" must not be accepted as a local redirect.

    urlparse reports no netloc for it, so without an explicit check the only thing
    preventing an off-site redirect is Werkzeug percent-encoding the backslash in
    the Location header -- someone else's escaping doing our security.
    """
    from openblade.web_flask.app import _is_safe_redirect_target

    assert _is_safe_redirect_target("/devices") is True
    assert _is_safe_redirect_target("/\\evil.example") is False
    assert _is_safe_redirect_target("//evil.example") is False


def test_backend_error_does_not_echo_non_json_body() -> None:
    """A non-JSON upstream body must not be relayed into the operator's browser.

    _handle_backend_error flashes BackendError.detail, so echoing response.text put
    proxy/gunicorn HTML error pages -- internal paths, hostnames, stack frames -- on
    screen. Only the backend's own curated JSON detail is surfaced.

    Mutation check: restore `detail = response.text` and this fails.
    """

    class FakeResponse:
        status_code = 500
        text = "<html>Traceback: /srv/app/secret.py line 3, password=hunter2</html>"

        def json(self) -> object:
            raise ValueError("not json")

    client = BackendClient(base_url="http://backend")
    with pytest.raises(BackendError) as excinfo:
        client._raise_for_error(FakeResponse())  # type: ignore[arg-type]
    assert "hunter2" not in excinfo.value.detail
    assert "Traceback" not in excinfo.value.detail
    assert "500" in excinfo.value.detail


def test_backend_error_surfaces_curated_json_detail() -> None:
    """The curated FastAPI detail IS still shown -- error quality is not degraded."""

    class FakeResponse:
        status_code = 400
        text = ""

        def json(self) -> object:
            return {"detail": "Barcode ABC123L9 is not in the library"}

    client = BackendClient(base_url="http://backend")
    with pytest.raises(BackendError) as excinfo:
        client._raise_for_error(FakeResponse())  # type: ignore[arg-type]
    assert excinfo.value.detail == "Barcode ABC123L9 is not in the library"


def test_login_bucket_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-auth login-attempt dict must not grow without bound.

    It is keyed partly by a caller-supplied username, and _is_login_blocked sweeps
    the whole dict on every attempt, so an unbounded dict is both a memory leak and
    a self-amplifying CPU cost (measured: 50k keys -> ~13ms per login attempt).
    """
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    app = create_app(client_factory=FakeBackendClient)
    for index in range(web_flask_app._LOGIN_BUCKET_MAX_KEYS + 500):
        web_flask_app._record_login_failure(app, f"1.2.3.4:user{index}")
    bucket = web_flask_app._attempt_bucket(app)
    assert len(bucket) <= web_flask_app._LOGIN_BUCKET_MAX_KEYS


def test_headers_omit_credentials_when_unauthenticated() -> None:
    """The login call itself must not present a session cookie."""
    client = BackendClient(base_url="http://backend")
    headers = client._headers()
    assert "Authorization" not in headers
    assert "Cookie" not in headers


def test_compose_service_token_default_matches_backend_default() -> None:
    """docker-compose.yml repeats service_auth's dev token; keep them in step.

    web-flask must send the same service token `api` falls back to, or /aml/mount
    and /aml/unmount 403 in the default dev stack. The literal is duplicated into
    compose out of necessity (compose cannot import Python), so this test is what
    stops the two copies drifting.
    """
    from pathlib import Path

    from openblade.api.service_auth import _DEFAULT_SERVICE_TOKEN

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert (
        f"OPENBLADE_SERVICE_TOKEN: ${{OPENBLADE_SERVICE_TOKEN:-{_DEFAULT_SERVICE_TOKEN}}}" in text
    )


def test_compose_keeps_react_web_service_alongside_flask() -> None:
    """The Flask UI is additive. `web` (React) must not have been repurposed.

    The parked change rewired `web` itself to Dockerfile.web, which would have
    retired the React SPA as a side effect while frontend-build-test still gated
    it. Both surfaces are named in CLAUDE.md, so both stay.
    """
    from pathlib import Path

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "context: ./frontend" in text
    assert "dockerfile: Dockerfile.web" in text
