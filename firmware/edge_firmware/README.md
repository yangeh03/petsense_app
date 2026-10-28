# Edge inference pilot

Separate ESP32-S3 sketch; the existing petsense_esp32 acquisition firmware is
not replaced. IMU samples at 10 Hz; inference summaries publish once a second.
Sound classes: bark, howl, whine, other. Whine remains experimental (test
recall 41.94%, precision 32.50%). See ../edge_ml/README.md for provenance.

Install Arduino CLI using https://arduino.github.io/arduino-cli/latest/installation/ .
Reuse the board core and sensor libraries from ../README.md; this sketch was
verified with esp32 core 2.0.16 and FQBN esp32:esp32:esp32s3.
Copy secrets.example.h to secrets.h and set the local MQTT password.
Review broker, port and APN in edge_firmware.ino and device ID in board_config.h before flashing.

From firmware/:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32s3 edge_firmware
arduino-cli board list
arduino-cli upload --fqbn esp32:esp32:esp32s3 -p YOUR_PORT edge_firmware
```

Do not flash during an active recording. Startup does not enable SD capture.
No secrets, raw training audio, personal recordings or compiled binaries are
included. The weights are a research prototype; observe source licenses.

## UWB with board-side inference

Both sketches receive BU03 CmdM:4 frames with the same parser and wiring:
BU03 TX1 -> ESP32 GPIO42 (UART0 RX), shared GND, 115200 baud. GPIO43 remains
UART0 debug TX; do not connect it to BU03 RX1. Health stays on UART1 GPIO40/41
and the modem on UART2 GPIO17/18. Set the serial monitor to 115200.

An independent RX task drains UWB during IMU sampling, model inference, SD writes
and modem waits. A locked packet snapshot is attached to the 10 Hz raw sample
and copied into each 1 Hz `petsense/sensor/edge_v1` summary as `uwb`:
`valid`, `receivedMs`, `timer`, `tagId`, `anchorId`, `sequence`, `anchorMask`,
`kalmanEnabled`, `distanceMm`, `rawMm`, `kalmanMm`. Distance arrays contain eight
millimetre values. Missing UWB or a gap over 2000 ms yields `valid: false` while
action/sound summaries continue. Only the latest Tag is retained, as in the
acquisition sketch. USB CDC debug output is optional; UART0 input belongs only
to the UWB worker.

`PETSENSE_DEVICE_ID` defaults to `petsense-esp32-001`; overriding it also changes
the MQTT client ID, legacy telemetry topic and command topic. It must match the
claimed device and the server map/Tag binding. Do not run two physical boards
with the same MQTT client ID.

Deploy the accompanying server change before hardware acceptance: edge summaries
enter the existing UWB positioning engine and owner-scoped WebSocket/API path
without rerunning action inference. Firmware flashing alone does not update the
server. Existing edge summaries without `uwb` remain supported.

Verify after flashing: changing Anchor distances appear in `edge_v1.uwb`, Tag and
mask match the map binding, the App receives a server position, and disconnecting
BU03 makes the position stale without stopping action/sound updates. Reconnect
BU03 and test 10–30 minutes with 4G and SD activity. These require real hardware;
host tests and compilation do not establish electrical correctness.
