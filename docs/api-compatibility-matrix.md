# OpenBlade API Compatibility Matrix

Generated against the current FastAPI route inventory (`python3 docs/generate_inventory.py`).
The live inventory currently exposes **685** API routes in total, including **532 `/aml`** routes,
**38 `/iblade`** routes, and **20 `/api`** NAS-style routes.

> Status legend: ✅ Implemented · ⚠️ Partial · ❌ Missing · 🔒 Gated

## Quantum Scalar AML API Coverage

This table tracks the main Scalar i3/i6 AML compatibility surface. Some features exist under
OpenBlade-compatible alternate paths even when the exact Scalar path is not present.

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| /aml/users/login | POST | ✅ Implemented | Cookie-based session login (`sessionID`). |
| /aml/users/logout | DELETE | ❌ Missing | Logout is implemented as `DELETE /aml/users/login`. |
| /aml/users | GET | ✅ Implemented | Lists users, scoped by caller role. |
| /aml/users | POST | ✅ Implemented | Creates AML users, including service-role accounts. |
| /aml/user/{name} | GET/PUT/DELETE | ✅ Implemented | Per-user read/update/delete compatibility path. |
| /aml/media/list | GET | ⚠️ Partial | Inventory listing is implemented as `GET /aml/media`. |
| /aml/media/move | POST | 🔒 Gated | Requires web auth, service token, and uses `TapeOperationOrchestrator`. |
| /aml/media/maxAllowed | GET | ⚠️ Partial | Implemented as `GET /aml/media/operations/moveMedium/maxAllowed`. |
| /aml/media/reports/usage | GET | ✅ Implemented | Returns media usage report data. |
| /aml/physical/library/* | GET/PUT | ⚠️ Partial | Physical library endpoints are exposed under `/aml/physicalLibrary/*`. |
| /aml/physical/robot/* | GET/PUT/POST | ⚠️ Partial | Robot controls exist as `/aml/robot/{id}` and `/aml/robots`. |
| /aml/drives | GET | ✅ Implemented | Drive inventory listing is present. |
| /aml/drives/logs | GET | ✅ Implemented | Drive log retrieval is implemented. |
| /aml/drives/reports/activity | GET | ✅ Implemented | Activity reporting is implemented. |
| /aml/drives/reports/cleaning | GET | ✅ Implemented | Cleaning reporting is implemented. |
| /aml/drives/reports/utilization | GET | ✅ Implemented | Utilization reporting is implemented. |
| /aml/drives/firmware/images | GET/POST | ✅ Implemented | Firmware image inventory and upload are present. |
| /aml/drives/firmware/images/{name}/activate | PUT | ✅ Implemented | Firmware activation route is present. |
| /aml/partitions | GET | ✅ Implemented | Partition listing is implemented. |
| /aml/partition/{name} | GET/POST/PUT/DELETE | ✅ Implemented | Partition CRUD-style compatibility path is present. |
| /aml/partitions/policies/* | GET/PUT | ⚠️ Partial | Policy endpoints exist as `/aml/partitions/policy/*` (singular `policy`). |
| /aml/operations/mount | POST | ❌ Missing | Mount is implemented as `POST /aml/mount`. |
| /aml/operations/unmount | POST | ❌ Missing | Unmount is implemented as `POST /aml/unmount`. |
| /aml/operations/inventory | POST | ✅ Implemented | Inventory operation route is implemented. |
| /aml/operations/robotics/home | POST | ✅ Implemented | Robotics homing route is implemented. |
| /aml/operations/verify | POST | ✅ Implemented | Verification operation is implemented. |
| /aml/operations/format | POST | ⚠️ Partial | Destructive format flow exists as `POST /cartridges/format/confirm`; storage format also exists under `/aml/system/storage/format`. |
| /aml/system/info | GET | ✅ Implemented | System info route is present. |
| /aml/system/software | GET | ✅ Implemented | Software inventory route is present. |
| /aml/system/sensors | GET | ✅ Implemented | Sensor telemetry route is present. |
| /aml/system/snapshot | GET/POST | ✅ Implemented | Snapshot retrieval and creation are present. |

Approximate AML coverage for this tracked compatibility set: **~81%** (`22 implemented/gated + 6 partial across 31 tracked entries`).

## iBlade API Coverage

OpenBlade currently has broad iBlade compatibility coverage, with most of the expected surface
available at the documented `/iblade` paths.

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| /iblade/states | GET | ✅ Implemented | State code reference list. |
| /iblade/volstates | GET | ✅ Implemented | Volume state reference list. |
| /iblade/vgstates | GET | ✅ Implemented | Volume-group state reference list. |
| /iblade/jobstates | GET | ✅ Implemented | Job state reference list. |
| /iblade/reasons | GET | ✅ Implemented | General reason code list. |
| /iblade/vgreasons | GET | ✅ Implemented | Volume-group reason code list. |
| /iblade/messages | GET | ✅ Implemented | Message listing endpoint is present. |
| /iblade/messages/{message_id} | GET/DELETE | ✅ Implemented | Read and clear message endpoints are present. |
| /iblade/product | GET | ✅ Implemented | Product summary endpoint is present. |
| /iblade/product/{element} | GET | ✅ Implemented | Per-element product metadata is present. |
| /iblade/reports/configuration | GET | ✅ Implemented | Configuration report endpoint is present. |
| /iblade/reports/media | GET | ✅ Implemented | Media report endpoint is present. |
| /iblade/reports/media-count | GET | ✅ Implemented | Media count report endpoint is present. |
| /iblade/reports/volume-groups | GET | ✅ Implemented | Volume-group report endpoint is present. |
| /iblade/status/io | GET | ✅ Implemented | I/O status endpoint is present. |
| /iblade/status/open-messages | GET | ✅ Implemented | Open-message count endpoint is present. |
| /iblade/system/settings | GET/PUT | ✅ Implemented | Global system settings read/write endpoint is present. |
| /iblade/system/settings/{settingname} | GET/PUT | ✅ Implemented | Per-setting read/write endpoint is present. |
| /iblade/system/snapshot | POST | ✅ Implemented | Snapshot creation endpoint is present. |
| /iblade/system/reboot | POST | ✅ Implemented | Reboot command endpoint is present. |
| /iblade/system/save-configuration | POST | ✅ Implemented | Save configuration endpoint is present. |
| /iblade/system/restore-configuration | POST | ✅ Implemented | Restore configuration endpoint is present. |
| /iblade/system/fwupgrade | POST | ✅ Implemented | Firmware upgrade endpoint is present. |
| /iblade/operations/* | POST | ✅ Implemented | Assignment, merge, prepare-export, replicate, safe-repair, and repair operations are present. |
| /iblade/hosts | GET/PUT | ✅ Implemented | Host inventory and update route is present. |
| /iblade/network | GET/PUT | ✅ Implemented | Network configuration route is present. |
| /iblade/volume-groups | GET | ❌ Missing | Current implementation exposes indexed volume-group routes only. |
| /iblade/volume-groups/{index} | GET/PUT | ✅ Implemented | Indexed volume-group read/update is present. |

Approximate iBlade coverage for this tracked set: **~96%** (`27 implemented across 28 tracked entries`).

## NAS API Coverage

The `/api` namespace currently focuses on library CRUD, upload/download, and gateway management.
Several broader NAS management families still live outside `/api` or are not yet implemented.

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| /api/libraries | GET/POST | ✅ Implemented | Library listing and creation are present. |
| /api/libraries/{library_id} | GET/PUT/DELETE | ✅ Implemented | Library CRUD routes are present. |
| /api/pools/{pool_id}/upload | POST | ✅ Implemented | Pool upload/staging route is present. |
| /api/pools/{pool_id}/files | GET | ✅ Implemented | Pool file listing is present. |
| /api/files/{file_id}/download | GET | ✅ Implemented | File download route is present. |
| /api/files/{file_id}/checksum | GET | ✅ Implemented | File checksum route is present. |
| /api/files/{file_id} | DELETE | ✅ Implemented | File deletion route is present. |
| /api/gateway/config | GET | ✅ Implemented | Gateway configuration read route is present. |
| /api/gateway/status | GET | ✅ Implemented | Gateway status route is present. |
| /api/gateway/start | POST | ✅ Implemented | Gateway start control route is present. |
| /api/gateway/stop | POST | ✅ Implemented | Gateway stop control route is present. |
| /api/gateway/credentials | GET/POST | ✅ Implemented | Gateway credential management is present. |
| /api/gateway/credentials/{username} | PUT/DELETE | ✅ Implemented | Per-credential update/delete is present. |
| /api/gateway/inbox-paths | GET | ✅ Implemented | Enumerates allowed inbox path scopes. |
| /api/gateway/sessions | GET | ✅ Implemented | Gateway session inventory is present. |
| /api/pools | GET/POST | ❌ Missing | No top-level `/api/pools` CRUD routes yet. |
| /api/pools/{pool_id} | GET/PUT/DELETE | ❌ Missing | Pool management is limited to upload/file routes. |
| /api/storage-policies/* | GET/POST/PUT/DELETE | ❌ Missing | Storage-policy namespace is not present under `/api`. |
| /api/cache-drives/* | GET/POST/PUT/DELETE | ❌ Missing | Cache-drive namespace is not present under `/api`. |
| /api/restore/* | GET/POST | ⚠️ Partial | Restore workflow exists under `/restore`, not `/api/restore`. |
| /api/archive/* | GET/POST | ⚠️ Partial | Archive workflow exists under `/archive`, not `/api/archive`. |

Approximate NAS coverage for this tracked set: **~76%** (`15 implemented across 21 tracked entries, with 2 partial families currently exposed under alternate prefixes`).
