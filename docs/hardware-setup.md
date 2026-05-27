# Hardware setup

1. Connect the medium changer and tape drive to the host and confirm they appear in `lsscsi -g`.
2. Identify the changer sg device and tape drive device.
3. Set both `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true` only for explicit validation sessions.
4. Run `openblade hardware connect-i3` to validate guarded changer discovery and inventory wiring before any live workflow.
5. Run `openblade hardware validate-ltfs --device /dev/st0 --barcode ABC123L9` and add `--mount-point /mnt/ltfs --exercise-mounts` only when mount capability checks are intended.
6. Keep the application in mock mode until read-only inventory and LTFS validation succeed end-to-end.
7. Document barcode conventions and slot maps before allowing write workflows.
