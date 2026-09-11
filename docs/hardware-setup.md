# Hardware setup

1. Connect the medium changer and tape drive to the host and confirm they appear in `lsscsi -g`.
2. Identify the changer sg device (`/dev/sgN`) and the tape drive device (`/dev/nstN`).
3. Set both `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true` only for explicit validation sessions.
4. Run `openblade hardware connect-i3` to validate guarded changer discovery and inventory wiring before any live workflow.
5. Run `openblade hardware validate-ltfs --device /dev/nst0 --barcode ABC123L9` and add `--mount-point /mnt/ltfs --exercise-mounts` only when mount capability checks are intended.
6. Keep the application in mock mode until read-only inventory and LTFS validation succeed end-to-end.
7. Document barcode conventions and slot maps before allowing write workflows.

## Always use the no-rewind device node (`/dev/nstN`, never `/dev/stN`)

Linux exposes every tape drive twice: `/dev/stN` **rewinds the tape on every
close**, while `/dev/nstN` leaves the head where it is. LTFS keeps a partitioned
index and data area and closes the device between operations, so pointing it at a
rewinding node silently repositions the tape underneath it and can corrupt the
index or overwrite data. Every OpenBlade instruction, `OPENBLADE_DRIVE_DEVICES`
value, and `--device` flag must name `/dev/nstN`. Tool output (for example the
`/dev/st0` column in `lsscsi -g`) still shows the rewinding node — that is the
tool reporting the kernel's naming, not a device to pass to OpenBlade.

## Containerized hardware mode

Bare-metal in the project venv (Python 3.12) is the recommended bring-up path —
see `docs/runbooks/real-i3-bringup-plan.md`. The default `docker-compose.yml` has
**no device passthrough**, so `OPENBLADE_BACKEND=real` cannot work in a container
without the opt-in override `docker-compose.hardware.example.yml`. Copy it, delete
the drive entries you do not have, and run:

```
cp docker-compose.hardware.example.yml docker-compose.hardware.yml   # then edit
docker compose -f docker-compose.yml -f docker-compose.hardware.yml up -d
```

Read the comments at the top of that file first: it is only for hosts whose
hardware has already been validated bare-metal, and it needs `OPENBLADE_TAPE_GID`
set to the group that owns `/dev/sg*` and `/dev/nst*` on the host, because the
container drops to the unprivileged `openblade` user.
