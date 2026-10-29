#pragma once
#include <stddef.h>
#include <stdint.h>

// A7670E CGPSINFO coordinates are degrees/minutes; speed is in knots.
struct GnssFix {
  bool valid = false;
  double latitude = 0, longitude = 0;
  double altitudeM = 0, speedMps = 0, courseDeg = 0;
  bool hasAltitude = false, hasSpeed = false, hasCourse = false;
  char utc[25] = {};
  uint32_t receivedMs = 0, sequence = 0;
};
bool parseGnssInfo(const char *line, GnssFix &fix);

// Called only by the existing modem owner. Never creates another UART reader.
class GnssController {
 public:
  void reset(uint32_t now);
  void feed(char ch, uint32_t now);
  // Returns a command only when idle; caller must send it before any MQTT AT.
  const char *poll(uint32_t now);
  bool busy() const { return waiting || cooling; }
  const GnssFix &latest() const { return fix; }
  const char *status(uint32_t now) const;
 private:
  GnssFix fix, candidate;
  char line[256] = {};
  size_t used = 0;
  bool overflow = false, waiting = false, cooling = false, gotInfo = false;
  bool failed = false;
  uint8_t stage = 0;
  uint32_t started = 0, due = 0;
  void finish(bool ok, uint32_t now);
  void consume(uint32_t now);
};
