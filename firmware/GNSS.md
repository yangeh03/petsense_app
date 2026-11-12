# A7670E GNSS through the existing 4G uplink

Both standalone sketches enable GNSS by default. No additional ESP32 UART or
MQTT topic is required. `edge_firmware` includes GPS in the 1 Hz `edge_v1`
summary and SD journal; `petsense_esp32` includes it in each compact batch sample
and full snapshot. Existing IMU, health, audio and UWB fields remain available.

The Arduino loop owns the modem UART. It sends `AT+CGNSSTST=0` to disable
continuous NMEA, then `AT+CGNSSPWR=1`, waits five seconds and queries
`AT+CGPSINFO` every five seconds. A query has a ten second deadline; MQTT AT
transactions wait until it finishes. On failure, MQTT resumes after a one
second drain interval and GNSS initialization retries after sixty seconds.
The edge sampling worker continues independently while queries are pending.
Set `PETSENSE_GNSS_ENABLED=0` at compile time to disable this feature.

`gps` contains `source: A7670E_GNSS`, `crs: WGS84`, `enabled`, `valid`,
`status`, `latitude`, `longitude`, `utc`, `altitudeM`, `speedMps`, `courseDeg`,
`receivedMs` and `sequence`. Decimal coordinates preserve hemisphere signs;
speed is converted from modem knots to metres/second. No fix uses JSON null
coordinates. Repeated GNSS UTC values do not refresh the fix timestamp. A fix
expires after fifteen seconds. Server normalization includes batch waiting
time and adds `ageMs` / `remainingMs`; clients expire it after receipt as well.

The existing authenticated latest telemetry API and device-scoped WebSocket
carry this object. Public laboratory debug and legacy SSE endpoints omit GPS. App Monitor
details display fix status, WGS84 latitude/longitude and UTC. The Location tab
also shows a Leaflet GPS map alongside indoor UWB (see `docs/GPS_LOCATION.md`); these coordinates
are independent of the indoor UWB map coordinate system.

For hardware verification: connect the antenna to **GNSS**, place it with a
clear sky view, connect the ESP32 CH343 flashing port, flash the selected
sketch, then inspect authenticated telemetry for a new UTC and changing
sequence. Verify loss of fix / stale coordinates disappear while IMU and UWB
continue uploading. Hardware verification is pending until the board is
connected; compilation and simulated AT replies do not establish a real fix.

Board command reference:
https://docs.waveshare.net/ESP32-S3-A7670E-4G/Windows/

Host regression: `python -m unittest discover -s firmware/tests`.
ESP32 compile check: `bash firmware/check-build.sh` (core 2.0.16).
