#pragma once

#include <Arduino.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy secrets.example.h to secrets.h and set your MQTT password before compiling."
#endif

// =====================================================
// 引脚定义
// =====================================================

// ---------- 麦克风 PDM ----------
static const int MIC_CLK_PIN  = 13;   // PDM CLK
static const int MIC_DATA_PIN = 14;   // PDM DATA

// ---------- IMU I2C ----------
static const int IMU_SCL_PIN = 7;     // 真实接线：SCL = GPIO7
static const int IMU_SDA_PIN = 1;     // 真实接线：SDA = GPIO1
static const uint8_t IMU_I2C_ADDRESS = 0x23;
static const uint32_t IMU_I2C_FREQ = 100000;
// Waveshare ESP32-S3-A-SIM7670X-4G V2 examples use SDA=GPIO15, SCL=GPIO16 for MAX17048.
static const int BATTERY_SDA_PIN = 15;
static const int BATTERY_SCL_PIN = 16;
static const uint8_t BATTERY_I2C_ADDRESS = 0x36;
static const uint32_t BATTERY_I2C_FREQ = 100000;
static const uint32_t BATTERY_READ_INTERVAL_MS = 1000;

// ---------- TF card / SDMMC ----------
static const int SDMMC_CLK_PIN = 5;
static const int SDMMC_CMD_PIN = 4;
static const int SDMMC_D0_PIN = 6;
static const int SDMMC_CD_PIN = 46;
static const bool LOCAL_SD_CAPTURE_MODE = false;
static const uint32_t SD_LOG_DURATION_MS = 0;  // 0 means manual stop from the dashboard.
static const uint32_t SD_SENSOR_LOG_INTERVAL_MS = 100;
static const uint32_t SD_HEALTH_LOG_INTERVAL_MS = 1000;
static const uint32_t SD_FLUSH_INTERVAL_MS = 5000;
static const uint32_t SD_AUDIO_SEGMENT_MS = 5000;
static const uint32_t SD_AUDIO_SEGMENT_RETRY_MS = 1000;
static const uint32_t SD_HARD_SAFETY_STOP_MS = 0;  // 0 means no automatic hard stop; stop from dashboard.
static const uint8_t SD_MAX_OPEN_FILES = 16;
static const int SDMMC_INIT_FREQ_KHZ = 400;

// ---------- 健康检测 UART ----------
static const int HEALTH_RX_PIN = 40;  // ESP32 RX <- health module TX
static const int HEALTH_TX_PIN = 41;  // ESP32 TX -> health module RX
static const uint32_t HEALTH_BAUD = 9600;

// ---------- A7670E 4G MQTT ----------
static const int MODEM_RX_PIN = 17;   // ESP32 RX <- A7670E TX
static const int MODEM_TX_PIN = 18;   // ESP32 TX -> A7670E RX
static const uint32_t MODEM_BAUD = 115200;

static const char *CELLULAR_APN = "ctnet";
static const char *MQTT_BROKER = "81.71.71.31";
static const uint16_t MQTT_PORT = 80;
static const char *MQTT_TOPIC = "petsense/sensor/esp32-001";
static const char *MQTT_AUDIO_TOPIC = "petsense/sensor/audio";
static const char *MQTT_AUDIO_FILE_TOPIC = "petsense/sensor/audio_file";
static const char *MQTT_AUDIO_WAV_TOPIC = "petsense/sensor/audio_wav";
static const char *MQTT_COMMAND_TOPIC = "petsense/device/esp32-001/command";
static const char *MQTT_USERNAME = "petsense-device";
static const char *MQTT_PASSWORD = PETSENSE_MQTT_PASSWORD;
static const char *MQTT_CLIENT_ID = "esp32-001";
static const char *DEVICE_ID = "petsense-esp32-001";
static const uint32_t MQTT_RETRY_INTERVAL_MS = 60000;
static const bool AUDIO_STREAM_ENABLED = false;
static const bool AUDIO_FILE_ENABLED = true;
static const bool AUDIO_FILE_SINGLE_UPLOAD_ENABLED = false;
static const uint16_t AUDIO_STREAM_SAMPLES = 1600;  // 100 ms at 16 kHz.
static const uint8_t AUDIO_STREAM_HEADER_LEN = 24;
static const uint16_t AUDIO_FILE_SAMPLE_RATE = 16000;
static const uint8_t AUDIO_FILE_SECONDS = 3;
static const uint8_t AUDIO_FILE_MAX_SECONDS = 60;
static const uint16_t AUDIO_FILE_SAMPLES_PER_READ = 1600;
static const uint8_t AUDIO_FILE_BITS_PER_SAMPLE = 16;
static const uint8_t AUDIO_FILE_BYTES_PER_SAMPLE = AUDIO_FILE_BITS_PER_SAMPLE / 8;
static const uint32_t AUDIO_FILE_BYTES = AUDIO_FILE_SAMPLE_RATE * AUDIO_FILE_SECONDS * AUDIO_FILE_BYTES_PER_SAMPLE;
static const uint32_t AUDIO_FILE_MAX_BYTES = AUDIO_FILE_SAMPLE_RATE * AUDIO_FILE_MAX_SECONDS * AUDIO_FILE_BYTES_PER_SAMPLE;
static const uint8_t AUDIO_FILE_WAV_HEADER_LEN = 44;
static const uint16_t AUDIO_FILE_CHUNK_RAW_BYTES = 4096;
static const uint8_t AUDIO_FILE_UPLOAD_BURST = 8;
static const int AUDIO_FILE_GAIN = 2;
static const uint32_t COMMAND_DEBOUNCE_MS = 800;
static const uint32_t CLOUD_SAMPLE_INTERVAL_MS = 100;
static const uint32_t CLOUD_PUBLISH_INTERVAL_MS = 1000;
static const uint8_t CLOUD_BATCH_MAX_FRAMES = 10;
static const uint32_t AUDIO_FEATURE_INTERVAL_MS = 50;
static const uint32_t AUDIO_FILE_UPLOAD_RETRY_MS = 3000;
static const uint32_t AUDIO_STREAM_LOG_INTERVAL_MS = 5000;
static const char *EVENT_DIR_IDS[] = {
  "01_standing",
  "02_sitting",
  "03_resting",
  "04_sleeping",
  "05_walking",
  "06_running",
  "07_eating",
  "08_rolling",
  "09_toilet",
  "10_jumping"
};
static const uint8_t EVENT_DIR_COUNT = sizeof(EVENT_DIR_IDS) / sizeof(EVENT_DIR_IDS[0]);
