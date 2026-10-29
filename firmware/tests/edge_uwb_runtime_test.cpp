// Exercise the actual RX worker and serializer with an in-memory UART and clock.
#include <cassert>
#include <deque>
#include <iostream>
#include <string>
#include <type_traits>
#include "uwb_protocol.h"

class String : public std::string {
public:
  using std::string::string;
  using std::string::operator+=;
  void remove(size_t offset) { erase(offset); }
  template<class T, std::enable_if_t<std::is_integral<T>::value, int> = 0>
  String &operator+=(T value) { append(std::to_string(value)); return *this; }
};
struct FakeUart {
  std::deque<uint8_t> bytes;
  bool available() { return !bytes.empty(); }
  int read() { int value = bytes.front(); bytes.pop_front(); return value; }
} DebugUart;
static uint32_t clockMs = 100;
static uint32_t millis() { return clockMs; }
using portMUX_TYPE = int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
#define pdMS_TO_TICKS(value) (value)
struct StopWorker {};
static void vTaskDelay(int) { throw StopWorker(); }
#include "uwb_runtime.h"

static void queueFrame() {
  uint8_t payload[91] = {};
  payload[0] = 123; payload[4] = 7; payload[6] = 2;
  payload[8] = 31; payload[9] = 7;
  const int32_t ranges[] = {1000, 2000, 3000, 0, 0, 0, 0, 0};
  for (size_t i = 0; i < 8; ++i) {
    for (size_t b = 0; b < 4; ++b) payload[10 + i * 4 + b] = (ranges[i] >> (b * 8)) & 255;
  }
  for (uint8_t b : {'C', 'm', 'd', 'M', ':', '4'}) DebugUart.bytes.push_back(b);
  DebugUart.bytes.push_back(sizeof(payload));
  for (uint8_t b : payload) DebugUart.bytes.push_back(b);
  const uint8_t tail[] = {0, '\r', '\n'};
  for (uint8_t b : tail) DebugUart.bytes.push_back(b);
}

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  if (mode == "wrap") clockMs = 0xfffffff0;
  if (mode != "empty") queueFrame();
  try { uwbWorker(nullptr); } catch (const StopWorker &) {}
  assert(DebugUart.bytes.empty());
  if (mode == "stale") clockMs += UWB_STALE_MS + 1;
  if (mode == "boundary") clockMs += UWB_STALE_MS;
  if (mode == "wrap") clockMs = 0x20;
  String payload("{\"deviceId\":\"collar\",\"action\":{\"label\":\"standing\"}}");
  appendUwbSnapshot(payload, snapshotUwb());
  std::cout << payload << '\n';
}
