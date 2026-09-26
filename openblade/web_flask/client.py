from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import Device, parse_device


class BackendError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 8.0,
        service_token: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.service_token = service_token
        self.api_token = api_token

    def _headers(
        self, token: str | None = None, *, library_id: int | None = None
    ) -> dict[str, str]:
        """Build headers carrying BOTH credentials the backend may demand.

        The backend authenticates its two surfaces independently
        (openblade.api.api_auth): the OpenBlade-native surface (/api, /jobs,
        /nas, /catalog, /status, /system, /volume-groups, /archive, /restore) is
        gated by the static ``OPENBLADE_API_TOKEN`` bearer, while /aml/* keeps its
        own per-user session from ``openblade.api.routes_aml_auth.require_auth``.
        ``token`` here is the AML *session* token minted by our /login -- it is NOT
        the native API token, so sending it as the only bearer made every native
        call 401 as soon as an operator enabled OPENBLADE_API_TOKEN (21 of the 46
        endpoints this client uses, i.e. all of Storage/Jobs/Reports/System).

        So: the session token travels as the ``sessionID`` cookie, which
        ``require_auth`` checks FIRST, leaving ``Authorization`` free for the
        native token. When no native token is configured we keep putting the
        session token in ``Authorization`` as before, so behaviour is unchanged
        for deployments that leave native auth disabled.
        """
        headers = {"Accept": "application/json"}
        bearer = self.api_token or token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if token:
            headers["Cookie"] = f"sessionID={token}"
        if self.service_token:
            headers["X-Openblade-Service-Token"] = self.service_token
        if library_id is not None:
            headers["X-OpenBlade-Library-Id"] = str(library_id)
        return headers

    def _raise_for_error(self, response: httpx.Response) -> None:
        if response.status_code < HTTPStatus.BAD_REQUEST:
            return
        detail = "Backend request failed"
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload.get("message") or detail)
        except ValueError:
            detail = response.text or detail
        raise BackendError(response.status_code, detail)

    def login(self, *, username: str, password: str) -> str:
        payload = {"name": username, "password": password}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/users/login",
                json=payload,
                headers=self._headers(),
            )
        self._raise_for_error(response)
        body = response.json()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise BackendError(
                HTTPStatus.BAD_GATEWAY, "Backend login response did not include a token"
            )
        return token

    def list_devices(self, token: str) -> list[Device]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/api/libraries",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [parse_device(item) for item in body if isinstance(item, dict)]

    def get_device(self, token: str, device_id: int) -> Device | None:
        for item in self.list_devices(token):
            if item.id == device_id:
                return item
        return None

    def create_device(self, token: str, payload: dict[str, Any]) -> Device:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/api/libraries",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            raise BackendError(HTTPStatus.BAD_GATEWAY, "Backend create-device response was invalid")
        return parse_device(body)

    def update_device(self, token: str, device_id: int, payload: dict[str, Any]) -> Device:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.put(
                f"{self.base_url}/api/libraries/{device_id}",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            raise BackendError(HTTPStatus.BAD_GATEWAY, "Backend update-device response was invalid")
        return parse_device(body)

    def list_jobs(
        self,
        token: str,
        *,
        library_id: int | None = None,
        state: str | None = None,
        job_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if library_id is not None:
            params["library_id"] = library_id
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/jobs/",
                params=params,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        jobs = [item for item in body if isinstance(item, dict)]
        if state:
            jobs = [item for item in jobs if str(item.get("state", "")).lower() == state.lower()]
        if job_type:
            jobs = [
                item for item in jobs if str(item.get("job_type", "")).lower() == job_type.lower()
            ]
        return jobs

    def list_media(self, token: str, *, library_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/media",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("mediaList", {}).get("media", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items[:limit] if isinstance(item, dict)]

    def list_drives(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/drives",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("driveList", {}).get("drive", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def list_partitions(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/partitions",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("partitionList", {}).get("partition", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def trigger_inventory(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/inventory",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {"message": "Inventory started"}

    def move_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        source: str,
        destination: str,
        barcode: str | None = None,
    ) -> dict[str, Any]:
        move_payload: dict[str, Any] = {"source": source, "destination": destination}
        if barcode:
            move_payload["barcode"] = barcode
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/operations/move",
                json={"move": move_payload},
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def mount_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcode: str,
        drive: str,
        partition: str | None = None,
    ) -> dict[str, Any]:
        mount_payload: dict[str, Any] = {"barcode": barcode, "drive": drive}
        if partition:
            mount_payload["partition"] = partition
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/mount",
                json={"mount": mount_payload},
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def unmount_media_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcode: str,
        drive: str,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/unmount",
                json={"unmount": {"barcode": barcode, "drive": drive}},
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def start_import_operation(
        self,
        token: str,
        *,
        library_id: int,
        partition: str,
        ie_station: str,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/import",
                json={"import": {"partition": partition, "ieStation": ie_station}},
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def start_export_operation(
        self,
        token: str,
        *,
        library_id: int,
        barcodes: list[str],
        ie_station: str,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/export",
                json={"export": {"barcodes": barcodes, "ieStation": ie_station}},
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_mounts(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/mounts",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("mountList", {}).get("mount", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def list_ie_stations(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/ieStations",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("ieStationList", {}).get("ieStation", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def get_inventory_status(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/operations/inventory/status",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        status = body.get("inventoryStatus")
        if isinstance(status, dict):
            return status
        return body

    def get_import_status(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/operations/import/status",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        status = body.get("importStatus")
        if isinstance(status, dict):
            return status
        return body

    def get_export_status(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/operations/export/status",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        status = body.get("exportStatus")
        if isinstance(status, dict):
            return status
        return body

    def list_media_pools(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/media/pools",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("poolList", {}).get("pool", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def list_nas_shares(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/shares",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def list_nas_pools(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/pools",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def list_aml_users(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/users",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("user", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def create_aml_user(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/users",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_nas_policies(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/policies",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def create_or_update_nas_policy(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/nas/policies",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_cache_drives(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/cache-drives",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def create_or_update_cache_drive(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/nas/cache-drives",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_source_stream_config(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/source-stream",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def update_source_stream_config(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.put(
                f"{self.base_url}/nas/source-stream",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_gateway_config(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/api/gateway/config",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_gateway_status(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/api/gateway/status",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_gateway_credentials(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/api/gateway/credentials",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def get_event_summary(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/events/summary",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        summary = body.get("eventSummary")
        if isinstance(summary, dict):
            return summary
        return body

    def get_alert_summary(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/alerts/summary",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        summary = body.get("alertSummary")
        if isinstance(summary, dict):
            return summary
        return body

    def list_events(
        self,
        token: str,
        *,
        library_id: int,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
        if severity and severity != "all":
            params["severity"] = severity
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/events",
                params=params,
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("eventList", {}).get("event", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def get_system_status(self, token: str, *, library_id: int | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/system/status",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, dict):
            return {}
        status = body.get("systemStatus")
        if isinstance(status, dict):
            return status
        return body

    def get_health(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/healthz",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_system_config(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/system/config-summary",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_catalog_status(self, token: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/status/catalog",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def get_library_status(self, token: str, *, library_id: int) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/status/library",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_catalog_files(self, token: str, *, limit: int = 25) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/catalog/",
                params={"limit": max(1, min(limit, 100))},
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {"files": [], "total": 0}

    def list_nas_datasets(
        self,
        token: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
        if status:
            params["status"] = status
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/nas/datasets",
                params=params,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def verify_nas_dataset(self, token: str, *, dataset_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/nas/datasets/{dataset_id}/verify",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def create_archive_job(
        self, token: str, *, source_path: str, volume_group: str
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/archive/",
                json={"source_path": source_path, "volume_group": volume_group},
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def create_restore_job(
        self, token: str, *, catalog_path: str, dest_path: str
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/restore/",
                json={"catalog_path": catalog_path, "dest_path": dest_path},
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def probe_device_endpoint(
        self,
        *,
        connection_url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        parsed = urlparse(connection_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BackendError(HTTPStatus.BAD_REQUEST, "Invalid device URL")
        base_url = connection_url.rstrip("/")
        timeout = min(self.timeout_seconds, 5.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            reachable = False
            for path in ("/healthz", "/health", "/aml/system/status"):
                try:
                    response = client.get(
                        f"{base_url}{path}", headers={"Accept": "application/json"}
                    )
                except httpx.HTTPError:
                    continue
                if response.status_code < HTTPStatus.BAD_REQUEST:
                    reachable = True
                    break
            if not reachable:
                raise BackendError(HTTPStatus.BAD_GATEWAY, "Unable to reach device health endpoint")
            authenticated = False
            if username:
                login_response = client.post(
                    f"{base_url}/aml/users/login",
                    json={"name": username, "password": password or ""},
                    headers={"Accept": "application/json"},
                )
                if login_response.status_code >= HTTPStatus.BAD_REQUEST:
                    raise BackendError(HTTPStatus.BAD_REQUEST, "Device authentication check failed")
                authenticated = True
        return {"reachable": True, "authenticated": authenticated}

    def list_volume_groups(self, token: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/volume-groups/",
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    def create_nas_pool(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/nas/pools",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def create_nas_share(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/nas/shares",
                json=payload,
                headers=self._headers(token),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def list_magazines(self, token: str, *, library_id: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/magazines",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("magazineList", {}).get("magazine", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def list_magazine_slots(
        self, token: str, *, library_id: int, magazine_id: str
    ) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/aml/magazine/{magazine_id}/slots",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        items = body.get("slotList", {}).get("slot", []) if isinstance(body, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def eject_magazine(self, token: str, *, library_id: int, magazine_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/magazine/{magazine_id}/eject",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    def insert_magazine(self, token: str, *, library_id: int, magazine_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/aml/magazine/{magazine_id}/insert",
                headers=self._headers(token, library_id=library_id),
            )
        self._raise_for_error(response)
        body = response.json()
        if isinstance(body, dict):
            return body
        return {}

    @staticmethod
    def device_payload(device: Device) -> dict[str, Any]:
        return asdict(device)
