# PetSense edge inference pilot

## Scope

This is a separate firmware (`../edge_firmware`), preserving the original
`../collection_firmware` for rollback. It does not start SD data collection.
Existing card journals are not deleted. In this mode historical journal replay
and high-rate cloud uploads are disabled; sampling continues locally at 10 Hz.

The reference TinyML_Demo uses a windowed MLP. This pilot trains two small
two-hidden-layer ReLU MLPs using scikit-learn and exports learned float32 weights
to C arrays. ESP32 executes dense layers and softmax locally, without a cloud
classifier or a TensorFlow runtime dependency. Model forward-pass parity and
the exact audio feature extractor have independent checks, including board
startup self-tests. Do not interpret offline tests as acoustic playback tests.

## Motion

- Source: Marcato et al., Inertial sensor dataset for Dog Posture Recognition,
  Mendeley DOI 10.17632/mpph6bmn7g.1, CC BY 4.0.
- https://data.mendeley.com/datasets/mpph6bmn7g/1
- Chest accelerometer and gyroscope ONLY. 100 Hz -> 10 Hz with anti-alias
  polyphase resampling within contiguous, single-label, single-dog runs.
- 2-second windows; six channel means and population standard deviations.
- Accelerometer unit g; gyro unit degrees/second. Firmware converts its rad/s.
- Split by dog before windows: 26 train, 8 validation, 8 test.
- Labels: standing, sitting, lying down, walking. Body shake is excluded from
  the final model because the initial five-class trial had zero test recall
  and only seven held-out shake windows.
- Chest orientation and sensor frequency response may differ on the real dog.
  No claim of collar placement validity or subject-independent field accuracy.

## Dog vocalization

The abandoned ESC-50 environmental-sound experiment is NOT the requested model.
The current v3 model uses bark, howl, whine plus an other category. Growl is
excluded from training and output, not merely hidden by the dashboard. It is
not guaranteed to be rejected when encountered in the real world.

- Dog play pant, Zenodo 18972388, expert-reviewed second-layer annotations.
  https://zenodo.org/records/18972388
- The Zenodo metadata says CC BY 4.0 but the included README says CC BY-NC 4.0.
  Restrict use to research/noncommercial pending author clarification.
- Supplement: Karaaslan et al. (2024), Voice Analysis in Dogs with Deep Learning,
  DOI 10.3390/s24247978, published Bark/Howl recording labels:
  https://github.com/mkaraaslan-dev/fully-automatic-voice-analysis-system
- Primary data have only 13 whine annotations and no expert-labeled howl events.
  Whine is exploratory. The Bark/Howl supplement has no dog IDs, so split by recording;
  individual-dog leakage and recording-domain confounding cannot be ruled out.
- The primary bark label includes yelps. Do not relabel these as emotions.
- Mono 16 kHz; remove frame DC offset; 256-sample symmetric Hann; power spectrum / 256; 40 triangular
  mel filters from 50 Hz to 8 kHz; natural log with epsilon 1e-9. Only a 16ms
  frame every 100ms, matching current firmware, not continuous audio coverage.
- Center each mel vector by its mean to reduce gain dependence. Use 1-second
  windows with feature means and population standard deviations.
- All overlapping windows of a primary dog stay in one split. Test MP3s are
  held-out excerpts, not training samples. Window-level scores are correlated
  within recordings and must not be described as independent animal accuracy.
- Softmax < 0.60 reports unknown. This is a heuristic, not calibrated confidence
  or a guarantee that arbitrary unseen sounds will be rejected.

### Additional whine data (v3)

`download_whine.py` downloads eight CC-licensed public Freesound previews,
about 76.55 seconds total from six uploaders (not eight independent dogs),
resamples to 16 kHz, and saves URLs, attribution, licenses, SHA256 hashes and
label provenance in `data/whine_fsd/manifest.json`. Most are author-labeled;
244448 also has positive FSD dog-whimper annotations. These are weak clip-level
labels, not expert time-localized annotations. Mixed growl/bark candidate clips
were excluded. Do not confuse AudioSet human Whimper with Whimper (dog).
Examples: https://freesound.org/people/esperri/sounds/118966/ and
https://freesound.org/people/jedg/sounds/505828/ .

Hold out uploader jedg for test and Breviceps for validation; all clips from
each other uploader stay in training. Exact downloaded-byte duplicates are
excluded. Uploader grouping is not proof of independent dog identities or
absence of near-duplicates across data sources. Train-only phase augmentation
is not additional independent recordings. Some data are CC BY-NC: research use.

### Current offline results

Motion: 88.29% accuracy on 3,782 windows from the eight held-out dogs.
Audio v3: macro F1 0.778 on 483 held-out windows; the 89.23% overall accuracy is
dominated by 366 other/background windows and MUST NOT be presented as the
accuracy of the three dog vocal types. Recall: bark 76.47% (51 windows), howl
97.14% (35), whine 41.94% (31). Whine precision is only 32.50%, so false positives
remain a serious limitation. Whine stays explicitly experimental on-device.
These scores use raw model argmax. Firmware additionally applies confidence
and quiet gates, so deployed recognition recall may be lower.
The old v2 whine recall was 0/4, but the new test set is larger and different:
do not interpret this as a controlled before/after improvement estimate.
Audio validation uses balanced accuracy for model selection. Test results were
inspected during this prototype iteration, so these are exploratory estimates,
not a pristine final benchmark. More independently labeled whine recordings and
real-device recordings are required before claiming reliable four-type sensing.
The final edge firmware and training both remove per-frame DC offset. Mean
AC RMS below 0.001 reports quiet (confidence zero); this is an engineering
noise gate, not a learned dog-vocal class or calibrated environmental threshold.

## Reproduce

Run scripts from the repository's `firmware/` directory using Python
with numpy, scipy, pandas, scikit-learn, joblib, requests, soundfile and pyserial.

1. `python edge_ml/download_imu_ranges.py`
2. `python edge_ml/download_dog_audio.py` (or ranged downloader `--vocal`)
3. `python edge_ml/download_bark_howl.py`
   Also run `python edge_ml/download_whine.py` before training v3 audio.
4. `python edge_ml/train.py imu`
5. `python edge_ml/train_dog_audio.py`
6. `python edge_ml/make_feature_test.py`
7. Copy `models/imu_model.h` and `models/audio_model.h` into `edge_firmware`.
8. `python edge_ml/test_pipeline.py`
9. `arduino-cli compile --fqbn esp32:esp32:esp32s3 --build-path edge_firmware/build edge_firmware`
10. Upload the validated build to the verified ESP32 serial port.

The ignored `secrets.h` must come from the local device configuration. Never
commit it, SSH passwords, environment files or exported personal recordings.

## Transport and server integration

One compact summary per second on `petsense/sensor/edge_v1`, with local action,
sound, health, battery, sample timestamps and diagnostics. Network arrivals may
be delayed during reconnects; do not claim a strict one-second network SLA.

Integration lives in `server/petsense/telemetry/edge.py` and the MQTT handler.
`/edge` is a diagnostic page requiring an existing PetSense session token.
`/api/app/edge/latest` returns only the logged-in account's claimed device data.
Tokens are held in page memory, not URLs or localStorage. Serve through HTTPS.
Shared device MQTT credentials do not authenticate individual device IDs;
per-device MQTT credentials/ACLs remain future work. Readings use the existing
database retention policy, not an unlimited training-data archive.
The Expo client is unchanged; its action/sound UI integration remains separate.
All deployment must go through GitHub review. Do not directly change the server.

## Device verification and review status

The v3 firmware compiled and uploaded successfully. Its DC-corrected feature
extractor matched the Python reference within 0.000023 in the previous v2 test.
Four local pipeline tests passed with the generated split manifest. A clean
checkout skips the split-manifest check until training regenerates that file.
Real MQTT summaries have been received with modelVersion `edge-dog-vocal-v3-whine`; the SD card is
mounted and capture remains inactive. This does not validate SD recording or
four-class recognition through speaker playback. Zero heart-rate/SpO2 values
must not be treated as valid physiological measurements.

The v3 device check received 67 summaries, median interval 1.000s, maximum
1.250s, with zero reported sampling gaps, queue drops or disk errors. Formal
capture remained off. This is a short connectivity check, not a reliability SLA.
The training joblib files use scikit-learn 1.7.2; do not load arbitrary untrusted
pickle/joblib files. Firmware uses C weights and needs no Python runtime.
