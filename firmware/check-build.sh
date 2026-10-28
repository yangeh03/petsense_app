#!/usr/bin/env bash
# Compile only; use a disposable sketch and a nonworking example credential.
set -euo pipefail
firmware_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build_root=$(mktemp -d)
trap 'rm -rf -- "$build_root"' EXIT
mkdir -p "$build_root/petsense_esp32"
cp "$firmware_root/petsense_esp32/"{petsense_esp32.ino,board_config.h,uwb_protocol.h,uwb_protocol.cpp} "$build_root/petsense_esp32/"
cp "$firmware_root/petsense_esp32/secrets.example.h" "$build_root/petsense_esp32/secrets.h"
arduino-cli compile --fqbn esp32:esp32:esp32s3 \
  --jobs 2 --build-path "$build_root/build" "$build_root/petsense_esp32"
