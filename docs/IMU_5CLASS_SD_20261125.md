# IMU five-class integration and SD records

Base: GitHub main `f62a9de`. Model: supplied
`PetSense_Edge_IMU_5class_20261125.zip`, version
`edge-imu-1s-5class-mapped-20261125`.

The model consumes 10 six-axis frames at 10 Hz (one-second windows).
Acceleration uses g; the existing gyro-to-degrees/second conversion is retained.
Classes: standing, sitting, lying down, walking, jumping. Confidence below 0.60
produces unknown; top_label preserves the unthresholded winner for review.
Audio inference uses its existing weights and has an independent window.
UWB, GNSS, health, battery and collection controls are retained from main.

## Journal schema

Recording is opt-in. Each existing CRC-framed `.psj` record still contains the
original imu accel/gyro/mag/quaternion/euler/barometer fields, audio features,
health, battery, UWB, GPS, seq, boot_id, mono_us and synchronized utc_us.
Invalid readings retain their existing validity flags; they are not valid data.
This change does not add raw audio PCM recording.

New fields on every raw frame:

- action_prediction_new: true only on a new IMU inference (nominally 1 Hz).
- action_prediction: null before warmup/after invalid inputs, otherwise the latest
  prediction with label, top_label, confidence, model_version, window_samples,
  seq, mono_us, utc_us and boot_id. Its timestamps identify the window's last raw
  sample, not the arrival time at the server. The first sequence is seq - 9.
  Repeated predictions are references, not additional independently classified
  windows. Human-corrected labels should be saved separately, not overwrite these.

The record envelope remains 12-byte little-endian magic/length/CRC32 followed by
UTF-8 JSON. CRC covers the enriched record. Oversized records still stop capture
and increment queue_drops rather than silently truncate.

## Start/stop and audit

The previously tested request_id acknowledgement fix is retained. Last command
ack is repeated in telemetry. Legacy callers get a CRC-derived correlation ID.
Stop applied/stopping must be followed by capture_active=false AND
storage_draining=false before removal. Arbitrary power loss can still lose the
unflushed tail; no filesystem can promise complete writes after arbitrary removal.

Boot no longer scans all historical files. Explicit collection_audit checks only
the requested session in the current boot, and reports through collection_status
audit.files: CRC errors, bytes/valid_bytes, rows, sequence gaps, IMU validity,
intervals outside 50-150 ms, and matching fresh prediction counts. Empty files
lists are not success. Do not run audit during capture.

`firmware/tests/verify_live_imu_sd.py` is an opt-in real-hardware test: wait for
new model and synchronized time, record 12 seconds, stop/drain, then request
readback and assert the result. It preserves all historical files and exports
evidence locally. Requires the local ignored secrets.h and paho-mqtt.

## Validation

ESP32 core 2.0.16 compile and flash verification passed. UWB regression: 20 tests
passed. After the SIM was topped up, real readback of session
`imu-verify-81649f1823e7` passed: 139 rows, sequence 5418 through 5556, 14 fresh
predictions, zero CRC errors/gaps/missing IMU/timing errors, all 362983 bytes valid.
This proves this short session, not arbitrary-duration or arbitrary-power-loss
integrity. Evidence is `sd-verification-imu-verify-81649f1823e7.json`.
Web/App changes are local only until PR
review and deployment; production server was not edited.
