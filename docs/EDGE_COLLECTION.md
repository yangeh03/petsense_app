# Edge dashboard SD collection

The `/edge` page still exposes only the designated lab device for public viewing.
Starting or stopping collection requires signing in with the PetSense account that
owns `petsense-esp32-001`. The page uses existing session ownership checks, not a
public control endpoint. Serve over HTTPS before entering account credentials.

## Rollout

- Deploy this PR through the normal reviewed GitHub workflow.
- Compile and separately flash `firmware/edge_firmware`; old firmware does not
  expose capacity/status and the page intentionally disables Start for it.
- No automatic recording is enabled. No SD files are erased or overwritten.
- The App stays on Expo SDK 57; this PR does not fix the SDK 58 Expo Go mismatch.

## Recording

Start creates an owned session and sends `collection_start`. The UI waits for a
newer board status with the matching session before reporting success. Stop waits
for inactive capture and a drained write queue. Commands have a debounce and an
acknowledgement timeout; timeout is not evidence the command failed to execute.
If offline, do not remove the card on the assumption that recording stopped.

The SD journal retains 10 Hz IMU/audio-feature/health snapshots with sequence,
monotonic and UTC timestamps and CRC. Health values update at the sensor's own
cadence. This is not raw audio recording. Cloud traffic remains compact 1 Hz
inference plus collection status; full journal replay remains disabled in edge
mode, so this feature does not create a complete cloud mirror of SD samples.

## Storage contract

`sd_total_bytes`, `sd_used_bytes`, `sd_free_bytes` are filesystem capacity in bytes,
not RAM or card marketing capacity. Unknown values are null, never fabricated zero.
The storage worker caches them about every 10 seconds; the sampling task never
queries FAT free space. Both summaries and collection status carry the fields.
The App monitor and collection page display MiB (1 MiB = 1048576 bytes).

`storage_draining` indicates queued/unflushed writes. A 1 MiB start reserve avoids
starting on an already full card, but is not a guarantee for a whole recording.
Write failure stops capture and reports errors. Flush is not a guarantee against
arbitrary power loss or card removal; stop and wait for acknowledgement first.

The collection command topic, clock/status envelope and app device ownership now
use `petsense-esp32-001` consistently; the MQTT client connection ID is unchanged.
No cloud deployment or board flashing is performed by the PR itself.
