#include "board_config.h"
#include "uwb_protocol.h"

#include <cassert>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

using Bytes = std::vector<uint8_t>;

static void writeLe(Bytes &bytes, size_t offset, uint32_t value, size_t count) {
  for (size_t i = 0; i < count; ++i) bytes[offset + i] = (value >> (8 * i)) & 0xff;
}

static Bytes frame(uint16_t tag = 7, bool kalman = true) {
  Bytes payload(91, 0);
  writeLe(payload, 0, 123456, 4);
  writeLe(payload, 4, tag, 2);
  writeLe(payload, 6, 2, 2);
  payload[8] = 31;
  payload[9] = 7;
  const int32_t raw[] = {1000, 2000, 3000, 0, 0, 0, 0, 0};
  const int32_t filtered[] = {1100, 0, 2900, 0, 0, 0, 0, 0};
  for (size_t i = 0; i < 8; ++i) {
    writeLe(payload, 10 + 4 * i, raw[i], 4);
    writeLe(payload, 43 + 4 * i, filtered[i], 4);
  }
  payload[42] = kalman;
  Bytes result = {'C', 'm', 'd', 'M', ':', '4', 91};
  result.insert(result.end(), payload.begin(), payload.end());
  result.insert(result.end(), {0, '\r', '\n'});
  return result;
}

static void feed(UwbProtocolParser &parser, const Bytes &bytes, uint32_t now = 100) {
  for (uint8_t byte : bytes) parser.push(byte, now);
}

static void arrayJson(const int32_t *values) {
  std::cout << '[';
  for (size_t i = 0; i < UWB_DISTANCE_COUNT; ++i) {
    if (i) std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  const Bytes bytes = frame();
  if (test == "split") {
    for (size_t split = 1; split < bytes.size(); ++split) {
      UwbProtocolParser parser;
      feed(parser, Bytes(bytes.begin(), bytes.begin() + split));
      assert(parser.parsedFrames() == 0);
      feed(parser, Bytes(bytes.begin() + split, bytes.end()));
      assert(parser.parsedFrames() == 1);
      assert(parser.latest().tagId == 7 && parser.latest().sequence == 31);
    }
  } else if (test == "distances") {
    UwbProtocolParser parser;
    feed(parser, bytes);
    assert(parser.latest().distanceMm[0] == 1100);
    assert(parser.latest().distanceMm[1] == 2000);
    assert(parser.latest().distanceMm[2] == 2900);
    feed(parser, frame(7, false));
    assert(parser.latest().distanceMm[0] == 1000);
  } else if (test == "stale") {
    UwbProtocolParser parser;
    assert(!parser.isFresh(0, UWB_STALE_MS));
    feed(parser, bytes);
    assert(parser.isFresh(100 + UWB_STALE_MS, UWB_STALE_MS));
    assert(!parser.isFresh(101 + UWB_STALE_MS, UWB_STALE_MS));
    feed(parser, bytes, 0xfffffff0);
    assert(parser.isFresh(0x20, UWB_STALE_MS));
  } else if (test == "recovery") {
    UwbProtocolParser parser;
    feed(parser, Bytes(1024, 0x55));
    Bytes bad = bytes;
    bad.back() = 0;
    feed(parser, bad);
    feed(parser, bytes);
    assert(parser.parsedFrames() == 1);
    assert(parser.invalidFrames() > 0 && parser.droppedBytes() > 0);
    // A complete but too-short payload must not overwrite the last good packet.
    feed(parser, {'C', 'm', 'd', 'M', ':', '4', 1, 0, 0, '\r', '\n'});
    assert(parser.parsedFrames() == 1 && parser.latest().tagId == 7);
  } else if (test == "latest") {
    UwbProtocolParser parser;
    feed(parser, bytes);
    feed(parser, bytes);
    assert(parser.parsedFrames() == 2);  // Deduplicate measurements downstream.
    feed(parser, frame(8));
    assert(parser.latest().tagId == 8);  // Current firmware retains one Tag only.
  } else if (test == "identity") {
    const std::string id = DEVICE_ID;
    assert(std::string(MQTT_TOPIC) == "petsense/sensor/" + id);
    assert(std::string(MQTT_COMMAND_TOPIC) == "petsense/device/" + id + "/command");
    assert(UWB_RX_PIN != HEALTH_RX_PIN && UWB_RX_PIN != HEALTH_TX_PIN);
    assert(UWB_RX_PIN != MODEM_RX_PIN && UWB_RX_PIN != MODEM_TX_PIN);
    assert(UWB_RX_PIN != DEBUG_TX_PIN);
  } else if (test == "contract") {
    UwbProtocolParser parser;
    feed(parser, bytes);
    const UwbPacket &p = parser.latest();
    // Mirror the compact u wire order in appendCloudBatchSample.
    std::cout << "{\"deviceId\":\"" << DEVICE_ID << "\",\"topic\":\"" << MQTT_TOPIC
              << "\",\"commandTopic\":\"" << MQTT_COMMAND_TOPIC << "\",\"u\":["
              << (parser.isFresh(200, UWB_STALE_MS) ? "true" : "false") << ','
              << p.receivedMs << ',' << p.timer << ',' << p.tagId << ',' << p.anchorId
              << ',' << unsigned(p.sequence) << ',' << unsigned(p.anchorMask) << ','
              << (p.kalmanEnabled ? "true" : "false") << ',';
    arrayJson(p.distanceMm); std::cout << ',';
    arrayJson(p.rawMm); std::cout << ',';
    arrayJson(p.kalmanMm); std::cout << "]}\n";
  } else {
    return 2;
  }
}
