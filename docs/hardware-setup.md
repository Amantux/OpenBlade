# Hardware setup

1. Connect the medium changer and tape drive to the host and confirm they appear in `lsscsi -g`.
2. Identify the changer sg device and tape drive device.
3. Validate discovery parsing with OpenBlade's discovery helpers before enabling real hardware.
4. Keep the application in mock mode until read-only inventory works end-to-end.
5. Document barcode conventions and slot maps before allowing write workflows.
