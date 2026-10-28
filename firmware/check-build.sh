#!/usr/bin/env bash
# Compile only; use a disposable sketch and a nonworking example credential.
set -euo pipefail
firmware_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build_root=$(mktemp -d)
trap 'rm -rf -- "$build_root"' EXIT
for sketch in petsense_esp32 edge_firmware; do
  mkdir -p "$build_root/$sketch"
  for source in "$firmware_root/$sketch/"*.ino "$firmware_root/$sketch/"*.h "$firmware_root/$sketch/"*.cpp; do
    [[ $(basename "$source") == secrets.h ]] || cp "$source" "$build_root/$sketch/"
  done
  cp "$firmware_root/$sketch/secrets.example.h" "$build_root/$sketch/secrets.h"
  arduino-cli compile --fqbn esp32:esp32:esp32s3 \
    --jobs 2 --build-path "$build_root/build-$sketch" "$build_root/$sketch"
done
