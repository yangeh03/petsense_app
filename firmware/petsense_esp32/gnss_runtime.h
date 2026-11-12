#pragma once
#include "gnss_protocol.h"

#ifndef PETSENSE_GNSS_ENABLED
#define PETSENSE_GNSS_ENABLED 1
#endif

void modemDrain(uint32_t durationMs);
void modemSendLine(const String &cmd);
static GnssController gnssController;
struct GnssSnapshot { GnssFix fix; char status[12]; };
static GnssSnapshot gnssSnapshot = {};
static portMUX_TYPE gnssMux = portMUX_INITIALIZER_UNLOCKED;

static void updateGnssSnapshot() {
  GnssSnapshot next={}; next.fix=gnssController.latest();
  snprintf(next.status,sizeof(next.status),"%s",PETSENSE_GNSS_ENABLED?gnssController.status(millis()):"disabled");
  portENTER_CRITICAL(&gnssMux); gnssSnapshot=next; portEXIT_CRITICAL(&gnssMux);
}
static GnssSnapshot snapshotGnss() {
  GnssSnapshot value;
  portENTER_CRITICAL(&gnssMux);value=gnssSnapshot;portEXIT_CRITICAL(&gnssMux);
  return value;
}
static void feedGnss(char ch) {
  if(PETSENSE_GNSS_ENABLED)gnssController.feed(ch,millis());
}
// True reserves the existing AT channel. Sampling workers never call this.
static bool serviceGnss() {
  if(!PETSENSE_GNSS_ENABLED || !modemReady)return false;
  const char *command=gnssController.poll(millis());
  if(command)modemSendLine(String(command));
  updateGnssSnapshot();
  return gnssController.busy();
}
static void appendGnssSnapshot(String &json, const GnssSnapshot &snapshot) {
  const GnssFix &fix=snapshot.fix;
  const bool valid=PETSENSE_GNSS_ENABLED && fix.valid && (uint32_t)(millis()-fix.receivedMs)<=15000;
  const char *status=valid?"fixed":fix.valid?"stale":snapshot.status[0]?snapshot.status:"starting";
  if(!json.endsWith("}"))return;
  json.remove(json.length()-1);
  json+=",\"gps\":{\"source\":\"A7670E_GNSS\",\"crs\":\"WGS84\",\"enabled\":";
  json+=PETSENSE_GNSS_ENABLED?"true":"false";
  json+=",\"valid\":";json+=valid?"true":"false";
  json+=",\"status\":\"";json+=status;
  json+="\",\"receivedMs\":";json+=String(fix.receivedMs);
  json+=",\"sequence\":";json+=String(fix.sequence);
  json+=",\"latitude\":";json+=valid?String(fix.latitude,7):String("null");
  json+=",\"longitude\":";json+=valid?String(fix.longitude,7):String("null");
  json+=",\"utc\":";json+=valid?String("\"")+fix.utc+"\"":String("null");
  json+=",\"altitudeM\":";json+=valid&&fix.hasAltitude?String(fix.altitudeM,2):String("null");
  json+=",\"speedMps\":";json+=valid&&fix.hasSpeed?String(fix.speedMps,3):String("null");
  json+=",\"courseDeg\":";json+=valid&&fix.hasCourse?String(fix.courseDeg,2):String("null");
  json+="}}";
}
