#pragma once
#include <Arduino.h>

// Same wiring and measurement timeout as petsense_esp32.
static const int UWB_RX_PIN = 42;
static const int DEBUG_TX_PIN = 43;
static const uint32_t UWB_BAUD = 115200;
static const uint32_t UWB_STALE_MS = 2000;
static const size_t UWB_UART_RX_BUFFER_SIZE = 4096;
static const int HEALTH_RX_PIN = 40;
static const int HEALTH_TX_PIN = 41;
static const int MODEM_RX_PIN = 17;
static const int MODEM_TX_PIN = 18;

#ifndef PETSENSE_DEVICE_ID
#define PETSENSE_DEVICE_ID "petsense-esp32-001"
#endif
static const char *DEVICE_ID = PETSENSE_DEVICE_ID;
static const char *MQTT_CLIENT_ID = PETSENSE_DEVICE_ID;
static const char *MQTT_TOPIC = "petsense/sensor/" PETSENSE_DEVICE_ID;
static const char *MQTT_COMMAND_TOPIC = "petsense/device/" PETSENSE_DEVICE_ID "/command";
