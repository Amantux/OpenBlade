# OpenBlade Controller Isolation Report

## Summary

OpenBlade separates browser-facing AML/iBlade/NAS requests from privileged controller actions.
The current codebase enforces session authentication broadly, adds a second shared-secret gate for
controller-only actions, and routes selected physical tape workflows through a dedicated
orchestration layer.

## Isolation Layers

### Layer 1: Web Session Auth (`require_auth`)
- User-facing AML, iBlade, gateway, and NAS routes typically depend on `require_auth`.
- Sessions are established by `POST /aml/users/login` and stored in the `sessionID` cookie.
- Sessions are invalidated by `DELETE /aml/users/login`.

### Layer 2: Service Token (`require_service_token`)
- Controller-only actions require the `X-Openblade-Service-Token` header.
- The expected token value comes from `OPENBLADE_SERVICE_TOKEN` and is compared with `secrets.compare_digest`.
- This extra gate is currently applied to robot/media movement, mount/unmount, drive unload, and format confirmation flows.

### Layer 3: TapeOperationOrchestrator
- `TapeOperationOrchestrator` is the dedicated choke point for audited tape hardware workflows.
- It provides per-drive and per-barcode locking plus persisted tape-op records.
- Today, it is used directly by `POST /aml/media/move` and by the `/tape-ops/*` orchestration endpoints.
- Other privileged AML compatibility handlers are still isolated by auth + service-token checks even when they do not yet delegate into the orchestrator.

## Protected Endpoints

| Endpoint | Auth Layer | Notes |
|----------|-----------|-------|
| POST /aml/media/move | `require_auth` + `require_service_token` + `TapeOperationOrchestrator` | Robot or load/unload movement workflow. |
| POST /aml/mount | `require_auth` + `require_service_token` | Tape loading compatibility endpoint. |
| POST /aml/unmount | `require_auth` + `require_service_token` | Tape unloading compatibility endpoint. |
| POST /aml/drive/{serialNumber}/unload | `require_auth` + `require_service_token` | Controller-only drive unload action. |
| POST /cartridges/format/confirm | `require_auth` + `require_service_token` | Destructive tape format confirmation. |

## Gateway Credential Isolation
- SFTP/SCP gateway credentials are managed by `openblade.nas.protocol_gateway`, not by AML web-session auth state.
- Gateway password verification uses `hmac.compare_digest` for timing-safe comparison.
- Gateway credentials are administered via authenticated `/api/gateway/*` routes but are not accepted by `POST /aml/users/login`.

## Path Traversal Prevention
- `file_id` parameters in upload/download routes are validated against a UUID4 regex before path resolution.
- Resolved paths are checked to remain under the configured staging or restore roots.
- Invalid IDs fail before filesystem access is allowed.

## Notable Boundary Gaps
- The orchestrator boundary is not yet universal: some AML compatibility handlers still mutate AML state directly after passing auth and service-token checks.
- Exact Scalar-compatible endpoint names are not always present even when equivalent OpenBlade functionality exists under alternate paths.
