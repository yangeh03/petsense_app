#pragma once

#include <Arduino.h>

static const size_t UWB_DISTANCE_COUNT = 8;

struct UwbPacket {
  bool valid;
  uint32_t timer;
  uint16_t tagId;
  uint16_t anchorId;
  uint8_t sequence;
  uint8_t anchorMask;
  bool kalmanEnabled;
  int32_t distanceMm[UWB_DISTANCE_COUNT];
  int32_t rawMm[UWB_DISTANCE_COUNT];
  int32_t kalmanMm[UWB_DISTANCE_COUNT];
  uint32_t receivedMs;
};

class UwbProtocolParser {
public:
  UwbProtocolParser();

  void push(uint8_t byteValue, uint32_t receivedMs);
  const UwbPacket &latest() const;
  bool isFresh(uint32_t nowMs, uint32_t staleAfterMs) const;
  uint32_t parsedFrames() const;
  uint32_t droppedBytes() const;
  uint32_t invalidFrames() const;

private:
  static const size_t RX_BUFFER_SIZE = 512;

  uint8_t rxBuffer_[RX_BUFFER_SIZE];
  size_t rxLength_;
  UwbPacket latest_;
  uint32_t parsedFrames_;
  uint32_t droppedBytes_;
  uint32_t invalidFrames_;

  void process(uint32_t receivedMs);
  void drop(size_t count);
  bool hasPrefix() const;
  bool parsePayload(const uint8_t *payload, uint8_t payloadLength, uint32_t receivedMs);
};
