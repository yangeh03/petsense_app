#pragma once
#include "board_config.h"
#include "uwb_protocol.h"

// The RX worker is the sole owner of UART0 input and the incremental parser.
// Sampling copies a complete packet under a short lock, then serializes outside it.
static portMUX_TYPE uwbMux = portMUX_INITIALIZER_UNLOCKED;
static UwbPacket uwbLatest = {};

static void uwbWorker(void *) {
  UwbProtocolParser parser;
  while (true) {
    uint32_t previous = parser.parsedFrames();
    // Bound each drain so a noisy/stuck sender cannot starve other core-0 work.
    for (size_t count = 0; count < UWB_UART_RX_BUFFER_SIZE && DebugUart.available(); ++count) {
      int value = DebugUart.read();
      if (value < 0) break;
      parser.push((uint8_t)value, millis());
    }
    if (parser.parsedFrames() != previous) {
      portENTER_CRITICAL(&uwbMux);
      uwbLatest = parser.latest();
      portEXIT_CRITICAL(&uwbMux);
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

static UwbPacket snapshotUwb() {
  UwbPacket packet;
  portENTER_CRITICAL(&uwbMux);
  packet = uwbLatest;
  portEXIT_CRITICAL(&uwbMux);
  return packet;
}

static void appendUwbSnapshot(String &payload, const UwbPacket &packet) {
  const uint32_t now = millis();
  payload.remove(payload.length() - 1);
  payload += ",\"uwb\":{\"valid\":";
  payload += packet.valid && (uint32_t)(now - packet.receivedMs) <= UWB_STALE_MS ? "true" : "false";
  payload += ",\"receivedMs\":"; payload += packet.receivedMs;
  payload += ",\"timer\":"; payload += packet.timer;
  payload += ",\"tagId\":"; payload += (unsigned int)packet.tagId;
  payload += ",\"anchorId\":"; payload += (unsigned int)packet.anchorId;
  payload += ",\"sequence\":"; payload += (unsigned int)packet.sequence;
  payload += ",\"anchorMask\":"; payload += (unsigned int)packet.anchorMask;
  payload += ",\"kalmanEnabled\":"; payload += packet.kalmanEnabled ? "true" : "false";
  const int32_t *arrays[] = {packet.distanceMm, packet.rawMm, packet.kalmanMm};
  const char *names[] = {"distanceMm", "rawMm", "kalmanMm"};
  for (size_t a = 0; a < 3; ++a) {
    payload += ",\""; payload += names[a]; payload += "\":[";
    for (size_t i = 0; i < UWB_DISTANCE_COUNT; ++i) {
      if (i) payload += ',';
      payload += arrays[a][i];
    }
    payload += ']';
  }
  payload += "}}";
}
