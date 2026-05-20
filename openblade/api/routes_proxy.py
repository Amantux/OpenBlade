from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.models import AmlUser

router = APIRouter(prefix="/aml/proxy", tags=["proxy"])


class RemoteLibraryProbeRequest(BaseModel):
    host: str
    port: int = Field(default=8000, ge=1, le=65535)
    username: str
    password: str


def _extract_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    value = _extract_nested(payload, *keys)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _count_online_drives(drives: list[dict[str, Any]]) -> int:
    return sum(
        1
        for drive in drives
        if str(drive.get("state") or drive.get("status") or "").upper() not in {"FAILED", "FAULTED", "OFFLINE"}
    )


def _count_active_jobs(jobs: list[dict[str, Any]]) -> int:
    return sum(1 for job in jobs if str(job.get("status") or "").upper() in {"PENDING", "RUNNING"})


def _count_used_slots(slots: list[dict[str, Any]]) -> int:
    return sum(1 for slot in slots if slot.get("barcode") or str(slot.get("state") or "").lower() != "empty")


def _build_remote_base_url(host: str, port: int) -> str:
    cleaned_host = host.strip().rstrip("/")
    if cleaned_host.startswith(("http://", "https://")):
        return cleaned_host
    return f"http://{cleaned_host}:{port}"


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pick_hostname(host: str, system_payload: dict[str, Any], info_payload: dict[str, Any]) -> str:
    return str(
        _extract_nested(system_payload, "systemInfo", "hostname")
        or info_payload.get("hostname")
        or _extract_nested(info_payload, "systemInfo", "hostname")
        or host
    )


def _pick_version(system_payload: dict[str, Any], version_payload: dict[str, Any], info_payload: dict[str, Any]) -> str:
    return str(
        _extract_nested(version_payload, "versionInfo", "software")
        or _extract_nested(version_payload, "versionInfo", "firmware")
        or _extract_nested(system_payload, "systemInfo", "firmware")
        or info_payload.get("version")
        or "unknown"
    )


def _pick_uptime(system_payload: dict[str, Any], info_payload: dict[str, Any]) -> int:
    value = _extract_nested(system_payload, "systemInfo", "uptime") or info_payload.get("uptime")
    return int(value) if isinstance(value, (int, float)) else 0


@router.post("/libraries/{library_id}/probe")
async def probe_remote_library(
    library_id: str,
    body: RemoteLibraryProbeRequest,
    _: AmlUser = Depends(require_auth),
) -> dict[str, Any]:
    del library_id
    base_url = _build_remote_base_url(body.host, body.port)

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            login_response = await client.post(
                f"{base_url}/aml/users/login",
                json={"name": body.username, "password": body.password},
            )
            if login_response.status_code != 200:
                payload = _response_json(login_response)
                detail = payload.get("detail") or payload.get("summary") or f"Login failed: {login_response.status_code}"
                return {"status": "error", "error": str(detail)}

            cookies = login_response.cookies
            requests = [
                client.get(f"{base_url}/aml/system", cookies=cookies),
                client.get(f"{base_url}/aml/system/info", cookies=cookies),
                client.get(f"{base_url}/aml/system/version", cookies=cookies),
                client.get(f"{base_url}/aml/drives", cookies=cookies),
                client.get(f"{base_url}/aml/jobs", cookies=cookies),
                client.get(f"{base_url}/aml/partitions", cookies=cookies),
            ]
            results = await asyncio.gather(*requests, return_exceptions=True)

            system_result, info_result, version_result, drives_result, jobs_result, partitions_result = results

            if isinstance(system_result, Exception) and isinstance(info_result, Exception):
                raise system_result

            system_ok = isinstance(system_result, httpx.Response) and system_result.is_success
            info_ok = isinstance(info_result, httpx.Response) and info_result.is_success
            if not system_ok and not info_ok:
                return {"status": "error", "error": "Unable to read remote library system information."}

            system_payload = _response_json(system_result) if isinstance(system_result, httpx.Response) else {}
            info_payload = _response_json(info_result) if isinstance(info_result, httpx.Response) else {}
            version_payload = _response_json(version_result) if isinstance(version_result, httpx.Response) else {}
            drives_payload = _response_json(drives_result) if isinstance(drives_result, httpx.Response) else {}
            jobs_payload = _response_json(jobs_result) if isinstance(jobs_result, httpx.Response) else {}
            partitions_payload = _response_json(partitions_result) if isinstance(partitions_result, httpx.Response) else {}

            partitions = _extract_list(partitions_payload, "partitionList", "partition")
            slot_requests = [
                client.get(f"{base_url}/aml/partition/{quote(str(partition.get('name', '')), safe='')}/slots", cookies=cookies)
                for partition in partitions
                if partition.get("name")
            ] + [
                client.get(f"{base_url}/aml/partition/{quote(str(partition.get('name', '')), safe='')}/ieSlots", cookies=cookies)
                for partition in partitions
                if partition.get("name")
            ]

            slot_responses = await asyncio.gather(*slot_requests, return_exceptions=True) if slot_requests else []
            slot_groups = [
                _extract_list(_response_json(response), "slotList", "slot")
                for response in slot_responses
                if isinstance(response, httpx.Response) and response.status_code == 200
            ]
            slots = [slot for group in slot_groups for slot in group]

            drives = _extract_list(drives_payload, "driveList", "drive") or [
                item for item in drives_payload.get("drives", []) if isinstance(item, dict)
            ]
            jobs = _extract_list(jobs_payload, "jobList", "job") or [
                item for item in jobs_payload.get("jobs", []) if isinstance(item, dict)
            ]

            return {
                "status": "online",
                "systemInfo": {
                    "hostname": _pick_hostname(body.host, system_payload, info_payload),
                    "version": _pick_version(system_payload, version_payload, info_payload),
                    "uptime": _pick_uptime(system_payload, info_payload),
                },
                "health": {
                    "drivesOnline": _count_online_drives(drives),
                    "slotsTotal": len(slots),
                    "slotsUsed": _count_used_slots(slots),
                    "activeJobs": _count_active_jobs(jobs),
                },
            }
    except Exception as exc:
        return {"status": "offline", "error": str(exc)}
