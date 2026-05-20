"""Stub FUSE hook for optional NAS virtual filesystem integration."""

from __future__ import annotations

from datetime import datetime

from openblade.nas.service import NasService
from openblade.nas.types import NasFileState


class FuseHook:
    """
    Stub for optional FUSE virtual filesystem integration.
    In v1, this is a no-op stub that records access attempts and returns
    appropriate offline/hydrating error codes without mounting anything.
    """

    def __init__(self, service: NasService):
        self.service = service
        self._access_log: list[dict] = []

    def _log_access(self, *, pool_id: str, logical_path: str, state: str, action: str) -> None:
        self._access_log.append(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "pool_id": pool_id,
                "path": logical_path,
                "state": state,
                "action": action,
            }
        )

    def on_file_open(self, pool_id: str, logical_path: str) -> dict:
        """
        Called when a virtual pool file is opened.
        - Look up file record
        - If state = ONLINE_CACHED: return {"action": "allow", "message": None}
        - If state = OFFLINE_ON_TAPE: log access attempt, return {"action": "queue_hydration", "message": "File is offline. Hydration queued.", "tape_barcode": barcode}
        - If state = HYDRATING: return {"action": "wait", "message": "File is being hydrated. Try again shortly."}
        - If state = MISSING_TAPE: return {"action": "error", "message": "Tape not available: {barcode}"}
        - If state = FAILED/CORRUPT: return {"action": "error", "message": "File is unavailable: {state}"}
        - If state = EXPORTED: return {"action": "error", "message": "File has been exported from the system."}
        - Log all accesses (timestamp, pool_id, path, state, action) to _access_log
        - If not found: return {"action": "error", "message": "File not found"}
        """
        try:
            record = self.service.get_pool_file_detail(pool_id, logical_path)
        except KeyError:
            result = {"action": "error", "message": "File not found"}
            self._log_access(pool_id=pool_id, logical_path=logical_path, state="not_found", action=result["action"])
            return result

        state = self.service.derive_file_state(record)
        barcode = record.tape_barcode
        if state is NasFileState.ONLINE_CACHED:
            result = {"action": "allow", "message": None}
        elif state is NasFileState.OFFLINE_ON_TAPE:
            result = {
                "action": "queue_hydration",
                "message": "File is offline. Hydration queued.",
                "tape_barcode": barcode,
            }
        elif state is NasFileState.HYDRATING:
            result = {"action": "wait", "message": "File is being hydrated. Try again shortly."}
        elif state is NasFileState.MISSING_TAPE:
            result = {"action": "error", "message": f"Tape not available: {barcode or 'unknown'}"}
        elif state is NasFileState.EXPORTED:
            result = {"action": "error", "message": "File has been exported from the system."}
        else:
            result = {"action": "error", "message": f"File is unavailable: {state.value}"}

        self._log_access(pool_id=pool_id, logical_path=logical_path, state=state.value, action=result["action"])
        return result

    def get_access_log(self) -> list[dict]:
        return list(self._access_log)

    def clear_access_log(self):
        self._access_log.clear()
