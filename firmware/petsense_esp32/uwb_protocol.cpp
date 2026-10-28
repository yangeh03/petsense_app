#include "uwb_protocol.h"

#include <string.h>

namespace {

static const uint8_t CMDM_PREFIX[] = {'C', 'm', 'd', 'M', ':', '4'};
static const size_t CMDM_PREFIX_LENGTH = sizeof(CMDM_PREFIX);
static const uint8_t MINIMUM_PAYLOAD_LENGTH = 75;

uint16_t readU16Le(const uint8_t *bytes) {
  return (uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8);
}

uint32_t readU32Le(const uint8_t *bytes) {
  return (uint32_t)bytes[0]
       | ((uint32_t)bytes[1] << 8)
       | ((uint32_t)bytes[2] << 16)
       | ((uint32_t)bytes[3] << 24);
}

int32_t readI32Le(const uint8_t *bytes) {
  return (int32_t)readU32Le(bytes);
}

}  // namespace

UwbProtocolParser::UwbProtocolParser()
    : rxLength_(0), latest_{}, parsedFrames_(0), droppedBytes_(0), invalidFrames_(0) {}

void UwbProtocolParser::push(uint8_t byteValue, uint32_t receivedMs) {
  if (rxLength_ >= RX_BUFFER_SIZE) {
    droppedBytes_ += rxLength_;
    rxLength_ = 0;
  }

  rxBuffer_[rxLength_++] = byteValue;
  process(receivedMs);
}

const UwbPacket &UwbProtocolParser::latest() const {
  return latest_;
}

bool UwbProtocolParser::isFresh(uint32_t nowMs, uint32_t staleAfterMs) const {
  return latest_.valid && (uint32_t)(nowMs - latest_.receivedMs) <= staleAfterMs;
}

uint32_t UwbProtocolParser::parsedFrames() const {
  return parsedFrames_;
}

uint32_t UwbProtocolParser::droppedBytes() const {
  return droppedBytes_;
}

uint32_t UwbProtocolParser::invalidFrames() const {
  return invalidFrames_;
}

void UwbProtocolParser::process(uint32_t receivedMs) {
  while (rxLength_ >= CMDM_PREFIX_LENGTH + 1) {
    if (!hasPrefix()) {
      size_t nextCandidate = 1;
      while (nextCandidate < rxLength_ && rxBuffer_[nextCandidate] != CMDM_PREFIX[0]) {
        nextCandidate++;
      }
      droppedBytes_ += nextCandidate;
      drop(nextCandidate);
      continue;
    }

    const uint8_t payloadLength = rxBuffer_[CMDM_PREFIX_LENGTH];
    const size_t frameLength = CMDM_PREFIX_LENGTH + 1 + payloadLength + 1 + 2;
    if (frameLength > RX_BUFFER_SIZE) {
      invalidFrames_++;
      droppedBytes_++;
      drop(1);
      continue;
    }
    if (rxLength_ < frameLength) {
      return;
    }

    if (rxBuffer_[frameLength - 2] != '\r' || rxBuffer_[frameLength - 1] != '\n') {
      invalidFrames_++;
      droppedBytes_++;
      drop(1);
      continue;
    }

    const uint8_t *payload = rxBuffer_ + CMDM_PREFIX_LENGTH + 1;
    if (parsePayload(payload, payloadLength, receivedMs)) {
      parsedFrames_++;
    } else {
      invalidFrames_++;
    }
    drop(frameLength);
  }
}

void UwbProtocolParser::drop(size_t count) {
  if (count >= rxLength_) {
    rxLength_ = 0;
    return;
  }

  memmove(rxBuffer_, rxBuffer_ + count, rxLength_ - count);
  rxLength_ -= count;
}

bool UwbProtocolParser::hasPrefix() const {
  if (rxLength_ < CMDM_PREFIX_LENGTH) {
    return false;
  }
  return memcmp(rxBuffer_, CMDM_PREFIX, CMDM_PREFIX_LENGTH) == 0;
}

bool UwbProtocolParser::parsePayload(const uint8_t *payload,
                                     uint8_t payloadLength,
                                     uint32_t receivedMs) {
  if (payloadLength < MINIMUM_PAYLOAD_LENGTH) {
    return false;
  }

  UwbPacket packet = {};
  packet.valid = true;
  packet.timer = readU32Le(payload);
  packet.tagId = readU16Le(payload + 4);
  packet.anchorId = readU16Le(payload + 6);
  packet.sequence = payload[8];
  packet.anchorMask = payload[9];

  for (size_t i = 0; i < UWB_DISTANCE_COUNT; i++) {
    packet.rawMm[i] = readI32Le(payload + 10 + 4 * i);
  }

  packet.kalmanEnabled = payload[42] != 0;
  for (size_t i = 0; i < UWB_DISTANCE_COUNT; i++) {
    packet.kalmanMm[i] = readI32Le(payload + 43 + 4 * i);
    packet.distanceMm[i] = packet.kalmanEnabled && packet.kalmanMm[i] > 0
                               ? packet.kalmanMm[i]
                               : packet.rawMm[i];
  }

  packet.receivedMs = receivedMs;
  latest_ = packet;
  return true;
}
