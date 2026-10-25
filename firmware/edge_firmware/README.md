# Edge inference pilot

Separate ESP32-S3 sketch; the existing petsense_esp32 acquisition firmware is
not replaced. IMU samples at 10 Hz; inference summaries publish once a second.
Sound classes: bark, howl, whine, other. Whine remains experimental (test
recall 41.94%, precision 32.50%). See ../edge_ml/README.md for provenance.

Install Arduino CLI using https://arduino.github.io/arduino-cli/latest/installation/ .
Reuse the board core and sensor libraries from ../README.md; this sketch was
verified with esp32 core 2.0.16 and FQBN esp32:esp32:esp32s3.
Copy secrets.example.h to secrets.h and set the local MQTT password.
Review broker, port, device ID and APN in edge_firmware.ino before flashing.

From firmware/:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32s3 edge_firmware
arduino-cli board list
arduino-cli upload --fqbn esp32:esp32:esp32s3 -p YOUR_PORT edge_firmware
```

Do not flash during an active recording. Startup does not enable SD capture.
No secrets, raw training audio, personal recordings or compiled binaries are
included. The weights are a research prototype; observe source licenses.
