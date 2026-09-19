#include <Arduino.h>
#include <Wire.h>
#include <FS.h>
#include <SD_MMC.h>
#include <math.h>
#include <string.h>
#include "driver/i2s.h"

// =====================================================
// 调试输出：UART0
// ESP32-S3 默认 UART0: TX=GPIO43, RX=GPIO44
// =====================================================
HardwareSerial DebugUart(0);

class DualDebugSerial : public Print {
public:
  explicit DualDebugSerial(HardwareSerial &uart) : _uart(uart) {}

  void begin(unsigned long baud, uint32_t config, int8_t rxPin, int8_t txPin) {
    Serial.begin(baud);
    _uart.begin(baud, config, rxPin, txPin);
  }

  size_t write(uint8_t value) override {
    size_t written = _uart.write(value);
    if (Serial) {
      Serial.write(value);
    }
    return written;
  }

  size_t write(const uint8_t *buffer, size_t size) override {
    size_t written = _uart.write(buffer, size);
    if (Serial) {
      Serial.write(buffer, size);
    }
    return written;
  }

  int available() {
    return (Serial ? Serial.available() : 0) + _uart.available();
  }

  int read() {
    if (Serial && Serial.available()) {
      return Serial.read();
    }
    return _uart.read();
  }

  void flush() {
    if (Serial) {
      Serial.flush();
    }
    _uart.flush();
  }

  int printf(const char *format, ...) {
    char buffer[256];
    va_list args;
    va_start(args, format);
    int len = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);

    if (len <= 0) {
      return len;
    }

    size_t toWrite = (len < (int)sizeof(buffer)) ? (size_t)len : sizeof(buffer) - 1;
    write((const uint8_t *)buffer, toWrite);
    return len;
  }

private:
  HardwareSerial &_uart;
};

DualDebugSerial DebugSerial(DebugUart);

#include "board_config.h"

TwoWire BatteryWire = TwoWire(1);
HardwareSerial HealthSerial(1);       // UART1, RX=GPIO40, TX=GPIO41
HardwareSerial ModemSerial(2);

// =====================================================
// 模式切换
// =====================================================
enum PlotMode {
  MODE_MIC,
  MODE_IMU,
  MODE_HEALTH,
  MODE_ALL
};

PlotMode plotMode = MODE_ALL;

// 健康模块绘图字段
enum HealthPlotField {
  HEALTH_PLOT_HR,
  HEALTH_PLOT_SPO2,
  HEALTH_PLOT_BODY_TEMP,
  HEALTH_PLOT_RESP
};

HealthPlotField healthPlotField = HEALTH_PLOT_HR;

// =====================================================
// 麦克风 I2S legacy
// =====================================================
#define I2S_PORT         I2S_NUM_0
#define MIC_SAMPLE_RATE  16000
#define I2S_READ_LEN     256
#define AUDIO_MEL_BANDS  40
#define AUDIO_FFT_BINS   (I2S_READ_LEN / 2 + 1)
#define AUDIO_MIN_HZ     50.0f
#define AUDIO_EPSILON    1.0e-9f

int16_t micSamples[I2S_READ_LEN];
int16_t audioStreamSamples[AUDIO_STREAM_SAMPLES];
uint8_t audioStreamPayload[AUDIO_STREAM_HEADER_LEN + AUDIO_STREAM_SAMPLES * sizeof(int16_t)];
int16_t audioFileReadSamples[AUDIO_FILE_SAMPLES_PER_READ];
uint8_t *audioFileSamples = NULL;
uint8_t audioFileChunkBuffer[AUDIO_FILE_CHUNK_RAW_BYTES];
float audioPower[AUDIO_FFT_BINS];
float audioMelFilters[AUDIO_MEL_BANDS][AUDIO_FFT_BINS];
float audioMel[AUDIO_MEL_BANDS];
float audioWindow[I2S_READ_LEN];
bool audioFeatureReady = false;
uint32_t audioFrameIndex = 0;
uint32_t audioStreamSeq = 0;
uint32_t audioFileId = 0;
uint32_t audioFileCaptureCount = 0;
uint32_t audioFileCapacitySamples = 0;
uint32_t audioFileUploadSamples = 0;
uint32_t audioFileUploadOffset = 0;
uint16_t audioFileUploadChunkIndex = 0;
uint16_t audioFileUploadTotalChunks = 0;
bool audioFileReady = false;
bool audioFileUploading = false;
bool audioFileStartSent = false;
bool audioFileRecording = false;
bool audioFileSingleAttempted = false;

// =====================================================
// IMU 厂家协议
// =====================================================
#define IMU_FUNC_VERSION        0x01
#define IMU_FUNC_RAW_ACCEL      0x04
#define IMU_FUNC_RAW_GYRO       0x0A
#define IMU_FUNC_RAW_MAG        0x10
#define IMU_FUNC_QUAT           0x16
#define IMU_FUNC_EULER          0x26
#define IMU_FUNC_BARO           0x32
#define IMU_FUNC_CALIB_IMU      0x70
#define IMU_FUNC_CALIB_MAG      0x71
#define IMU_FUNC_CALIB_BARO     0x72
#define IMU_FUNC_CALIB_TEMP     0x73
#define IMU_FUNC_REQUEST_DATA   0x80
#define IMU_FUNC_RETURN_STATE   0x81
#define IMU_FUNC_RESET_FLASH    0xA0
#define IMU_FUNC_REBOOT_DEVICE  0xA1

typedef struct {
  float accel[3];
  float gyro[3];
  float mag[3];
  float quat[4];
  float euler[3];
  float baro[4];
} imu_measurement_t;

imu_measurement_t imuData;
bool imuFound = false;
bool imuHasMag = true;
bool imuHasBaro = true;

// =====================================================
// 健康模块：24字节协议
// =====================================================
static const uint8_t HEALTH_CMD_START = 0x24;
static const uint8_t HEALTH_CMD_STOP  = 0x2A;

static const uint8_t HEALTH_FRAME_HEAD  = 0xFF;
static const uint8_t HEALTH_FRAME_FIXED = 0x01;
static const uint8_t HEALTH_FRAME_TAIL  = 0xF1;
static const int HEALTH_FRAME_LEN = 24;

bool healthStarted = false;

struct HealthPacket {
  uint8_t heartRate;       // 第3字节
  uint8_t spo2;            // 第4字节
  uint8_t microCir;        // 第5字节
  uint8_t systolic;        // 第6字节
  uint8_t diastolic;       // 第7字节
  uint8_t respiration;     // 第8字节
  uint8_t fatigue;         // 第9字节
  uint8_t rrInterval;      // 第10字节
  uint8_t hrvSdnn;         // 第11字节
  uint8_t hrvRmssd;        // 第12字节
  float bodyTemp;          // 第13,14字节
  float envTemp;           // 第15,16字节
  bool valid;
};

HealthPacket healthData = {0};

uint8_t healthRxBuf[HEALTH_FRAME_LEN];
int healthRxIndex = 0;
bool healthFrameReceiving = false;

struct BatteryPacket {
  bool found;
  bool valid;
  float voltage;
  float percent;
  uint16_t rawVcell;
  uint16_t rawSoc;
  uint32_t lastReadMs;
};

BatteryPacket batteryData = {false, false, 0.0f, 0.0f, 0, 0, 0};

static const int8_t IMA_INDEX_TABLE[16] = {
  -1, -1, -1, -1, 2, 4, 6, 8,
  -1, -1, -1, -1, 2, 4, 6, 8
};

static const int16_t IMA_STEP_TABLE[89] = {
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
  19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
  50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
  130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
  337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
  876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
  2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
  5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
  15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
};

// =====================================================
// 时间控制
// =====================================================
unsigned long lastMicUs = 0;
unsigned long lastImuMs = 0;
unsigned long lastHealthMs = 0;
unsigned long lastAllMs = 0;
unsigned long lastCloudPublishMs = 0;
unsigned long lastCloudSampleMs = 0;
unsigned long cloudBatchStartedMs = 0;
unsigned long lastAudioStreamLogMs = 0;
unsigned long lastBatteryLogMs = 0;
unsigned long lastMqttConnectAttemptMs = 0;
unsigned long lastMqttFailureLogMs = 0;
unsigned long lastAudioFeatureMs = 0;
unsigned long lastAudioFileUploadAttemptMs = 0;
unsigned long lastExperimentCommandMs = 0;
unsigned long lastRecordCommandMs = 0;
unsigned long lastEventCommandMs = 0;

bool modemReady = false;
bool mqttConnected = false;
bool mqttCommandSubscribed = false;
String modemAsyncBuffer;
bool audioStreamStatsReady = false;
String cloudBatchPayload;
uint8_t cloudBatchCount = 0;
float latestAudioRms = 0.0f;
float latestAudioZcr = 0.0f;

bool sdReady = false;
bool sdLoggingActive = false;
bool sdLoggingComplete = false;
String sdRunDir;
String sdRunId;
String sdFilePrefix = "petsense_";
File sdAudioFile;
File sdImuFile;
File sdHealthFile;
File sdAudioFeatureFile;
File sdBatteryFile;
File sdEventAudioFile;
File sdEventImuFile;
File sdEventHealthFile;
File sdEventAudioFeatureFile;
File sdEventBatteryFile;
uint32_t sdLogStartMs = 0;
uint32_t sdAudioDataBytes = 0;
uint32_t sdEventAudioDataBytes = 0;
uint32_t sdAudioSegmentBytes = 0;
uint32_t sdEventAudioSegmentBytes = 0;
uint32_t sdAudioSegmentStartMs = 0;
uint32_t sdEventAudioSegmentStartMs = 0;
uint32_t sdAudioSegmentIndex = 0;
uint32_t sdEventAudioSegmentIndex = 0;
uint32_t sdLastAudioSegmentRetryMs = 0;
uint32_t sdLastEventAudioSegmentRetryMs = 0;
uint32_t sdLastSensorLogMs = 0;
uint32_t sdLastHealthLogMs = 0;
uint32_t sdLastFlushMs = 0;
uint32_t sdRowsWritten = 0;
uint32_t lastSdStatusLogMs = 0;
String sdStatusMessage = "not started";
int sdCardDetectAtInit = -1;
bool sdEventActive = false;
String sdEventId;
String sdEventLabel;
String sdEventSegmentId;
String sdEventDir;
String sdEventFilePrefix;
bool deviceMicOK = false;
bool deviceAudioBufferOK = false;
bool deviceImuOK = false;
bool deviceBatteryOK = false;
String experimentId;
bool experimentActive = false;
uint32_t audioFileAutoStopBytes = 0;

// =====================================================
// 工具函数
// =====================================================
static int16_t toInt16LE(const uint8_t *bytes) {
  return (int16_t)((bytes[1] << 8) | bytes[0]);
}

static float toFloatLE(const uint8_t *bytes) {
  float value;
  memcpy(&value, bytes, sizeof(float));
  return value;
}

void serviceHealthModule();
bool readMicFrame(size_t &sampleCountOut);
bool computeAudioFeatures(const int16_t *samples, int n, float &rmsOut, float &zcrOut, float melOut[AUDIO_MEL_BANDS]);
bool IMU_I2C_ReadAllFlexible(imu_measurement_t *out);
bool mqttPublishRaw(const char *topic, const uint8_t *payload, size_t payloadLen);
bool publishAudioStreamFrame();
void captureAudioFeatureStep();
void captureAudioFileStep();
void processAudioFileUploadStep();
void startAudioRecording();
void stopAudioRecording();
void processModemAsyncInput();
bool initSdLogger(bool micOK, bool audioBufferOK, bool imuOK, bool batteryOK);
void sdLogStep();
void stopSdLogger();
bool startExperimentCapture(const String &id);
void stopExperimentCapture();
bool startEventCapture(const String &eventId, const String &label);
void stopEventCapture();

// =====================================================
// A7670E 4G / MQTT
// =====================================================
static void appendModemAsyncChar(char ch);
static bool handleModemAsyncCommands();

static void serviceLocalCaptureDuringModemWait() {
  if (sdLoggingActive) {
    sdLogStep();
  } else {
    serviceHealthModule();
  }
}

void modemDrain(uint32_t durationMs = 100) {
  uint32_t start = millis();
  while (millis() - start < durationMs) {
    while (ModemSerial.available()) {
      char ch = (char)ModemSerial.read();
      appendModemAsyncChar(ch);
    }
    handleModemAsyncCommands();
    serviceLocalCaptureDuringModemWait();
    delay(5);
  }
}

void modemSendLine(const String &cmd) {
  String debugCmd = cmd;
  debugCmd.replace(MQTT_PASSWORD, "********");
  DebugSerial.print("MODEM >>> ");
  DebugSerial.println(debugCmd);
  ModemSerial.print(cmd);
  ModemSerial.print("\r\n");
}

static void appendModemAsyncChar(char ch) {
  modemAsyncBuffer += ch;
  if (modemAsyncBuffer.length() > 2400) {
    modemAsyncBuffer.remove(0, modemAsyncBuffer.length() - 1200);
  }
}

static String extractJsonStringField(const String &src, const char *key) {
  String needle = "\"";
  needle += key;
  needle += "\"";
  int pos = src.indexOf(needle);
  if (pos < 0) {
    return "";
  }

  pos += needle.length();
  while (pos < (int)src.length() && isspace((unsigned char)src[pos])) pos++;
  if (pos >= (int)src.length() || src[pos] != ':') {
    return "";
  }
  pos++;
  while (pos < (int)src.length() && isspace((unsigned char)src[pos])) pos++;
  if (pos >= (int)src.length() || src[pos] != '"') {
    return "";
  }
  pos++;

  String value = "";
  bool escaped = false;
  for (; pos < (int)src.length(); pos++) {
    char ch = src[pos];
    if (escaped) {
      if (ch == 'n') value += '\n';
      else if (ch == 'r') value += '\r';
      else if (ch == 't') value += '\t';
      else value += ch;
      escaped = false;
    } else if (ch == '\\') {
      escaped = true;
    } else if (ch == '"') {
      break;
    } else {
      value += ch;
    }
  }

  value.replace("/", "_");
  value.replace(" ", "_");
  return value;
}

static bool modemAsyncHasCommand() {
  return modemAsyncBuffer.indexOf("experiment_start") >= 0 ||
         modemAsyncBuffer.indexOf("experiment_stop") >= 0 ||
         modemAsyncBuffer.indexOf("event_start") >= 0 ||
         modemAsyncBuffer.indexOf("event_stop") >= 0 ||
         modemAsyncBuffer.indexOf("record_audio_start") >= 0 ||
         modemAsyncBuffer.indexOf("record_audio_stop") >= 0;
}

static bool modemAsyncCommandComplete() {
  if (!modemAsyncHasCommand()) {
    return false;
  }
  return modemAsyncBuffer.indexOf("+CMQTTRXEND") >= 0;
}

static bool handleModemAsyncCommands() {
  if (modemAsyncHasCommand() && !modemAsyncCommandComplete()) {
    return false;
  }

  if (modemAsyncBuffer.indexOf("experiment_start") >= 0) {
    uint32_t now = millis();
    if (now - lastExperimentCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: experiment start ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastExperimentCommandMs = now;
    String id = extractJsonStringField(modemAsyncBuffer, "experimentId");
    if (!id.length()) id = extractJsonStringField(modemAsyncBuffer, "id");
    DebugSerial.print("MQTT command: experiment start id=");
    DebugSerial.println(id);
    startExperimentCapture(id);
    modemAsyncBuffer = "";
    return true;
  }

  if (modemAsyncBuffer.indexOf("experiment_stop") >= 0) {
    uint32_t now = millis();
    if (now - lastExperimentCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: experiment stop ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastExperimentCommandMs = now;
    DebugSerial.println("MQTT command: experiment stop");
    stopExperimentCapture();
    modemAsyncBuffer = "";
    return true;
  }

  if (modemAsyncBuffer.indexOf("event_start") >= 0) {
    uint32_t now = millis();
    if (now - lastEventCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: event start ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastEventCommandMs = now;
    String eventId = extractJsonStringField(modemAsyncBuffer, "eventId");
    if (!eventId.length()) eventId = extractJsonStringField(modemAsyncBuffer, "e");
    String label = extractJsonStringField(modemAsyncBuffer, "label");
    if (!label.length()) label = extractJsonStringField(modemAsyncBuffer, "l");
    DebugSerial.print("MQTT command: event start id=");
    DebugSerial.print(eventId);
    DebugSerial.print(" label=");
    DebugSerial.println(label);
    startEventCapture(eventId, label);
    modemAsyncBuffer = "";
    return true;
  }

  if (modemAsyncBuffer.indexOf("event_stop") >= 0) {
    uint32_t now = millis();
    if (now - lastEventCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: event stop ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastEventCommandMs = now;
    DebugSerial.println("MQTT command: event stop");
    stopEventCapture();
    modemAsyncBuffer = "";
    return true;
  }

  if (modemAsyncBuffer.indexOf("record_audio_start") >= 0) {
    uint32_t now = millis();
    if (now - lastRecordCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: record audio start ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastRecordCommandMs = now;
    DebugSerial.println("MQTT command: record audio start");
    startAudioRecording();
    modemAsyncBuffer = "";
    return true;
  }

  if (modemAsyncBuffer.indexOf("record_audio_stop") >= 0) {
    uint32_t now = millis();
    if (now - lastRecordCommandMs < COMMAND_DEBOUNCE_MS) {
      DebugSerial.println("MQTT command: record audio stop ignored by debounce");
      modemAsyncBuffer = "";
      return true;
    }
    lastRecordCommandMs = now;
    DebugSerial.println("MQTT command: record audio stop");
    stopAudioRecording();
    modemAsyncBuffer = "";
    return true;
  }

  int rxEnd = modemAsyncBuffer.indexOf("+CMQTTRXEND");
  if (rxEnd >= 0) {
    modemAsyncBuffer.remove(0, rxEnd + 11);
  }

  return false;
}

bool modemWaitFor(uint32_t timeoutMs, const char *a, const char *b = NULL, const char *c = NULL, const char *d = NULL) {
  String response;
  response.reserve(512);
  uint32_t start = millis();

  while (millis() - start < timeoutMs) {
    while (ModemSerial.available()) {
      char ch = (char)ModemSerial.read();
      response += ch;
      appendModemAsyncChar(ch);
      handleModemAsyncCommands();

      if (response.length() > 1800) {
        response.remove(0, response.length() - 900);
      }

      if (a && response.indexOf(a) >= 0) {
        DebugSerial.print("MODEM <<< ");
        DebugSerial.println(response);
        return true;
      }
      if (b && response.indexOf(b) >= 0) {
        DebugSerial.print("MODEM <<< ");
        DebugSerial.println(response);
        return true;
      }
      if (c && response.indexOf(c) >= 0) {
        DebugSerial.print("MODEM <<< ");
        DebugSerial.println(response);
        return true;
      }
      if (d && response.indexOf(d) >= 0) {
        DebugSerial.print("MODEM <<< ");
        DebugSerial.println(response);
        return true;
      }
    }
    serviceLocalCaptureDuringModemWait();
    yield();
    delay(10);
  }

  DebugSerial.print("MODEM timeout waiting for ");
  DebugSerial.println(a ? a : "(any)");
  if (response.length() > 0) {
    DebugSerial.print("MODEM partial <<< ");
    DebugSerial.println(response);
  }
  return false;
}

void processModemAsyncInput() {
  while (ModemSerial.available()) {
    char ch = (char)ModemSerial.read();
    appendModemAsyncChar(ch);
  }

  handleModemAsyncCommands();
}

void serviceModemAsyncFor(uint32_t durationMs) {
  uint32_t start = millis();
  while (millis() - start < durationMs) {
    processModemAsyncInput();
    serviceLocalCaptureDuringModemWait();
    yield();
    delay(10);
  }
}

bool modemCommand(const String &cmd, uint32_t timeoutMs = 3000, const char *ok = "OK") {
  modemDrain(20);
  modemSendLine(cmd);
  return modemWaitFor(timeoutMs, ok, "ERROR", "+CME ERROR", "+CMS ERROR");
}

bool modemCommandOk(const String &cmd, uint32_t timeoutMs = 3000) {
  modemDrain(20);
  modemSendLine(cmd);
  if (!modemWaitFor(timeoutMs, "OK")) {
    return false;
  }
  return true;
}

bool modemWaitPromptAndSend(const String &cmd, const String &data, uint32_t promptTimeoutMs = 5000, uint32_t okTimeoutMs = 5000) {
  modemDrain(20);
  modemSendLine(cmd);
  if (!modemWaitFor(promptTimeoutMs, ">")) {
    return false;
  }

  DebugSerial.print("MODEM >>> data length=");
  DebugSerial.println(data.length());
  ModemSerial.print(data);
  return modemWaitFor(okTimeoutMs, "OK");
}

bool modemWaitPromptAndWrite(const String &cmd, const uint8_t *data, size_t dataLen, uint32_t promptTimeoutMs = 5000, uint32_t okTimeoutMs = 8000) {
  modemDrain(20);
  modemSendLine(cmd);
  if (!modemWaitFor(promptTimeoutMs, ">")) {
    return false;
  }

  DebugSerial.print("MODEM >>> binary data length=");
  DebugSerial.println(dataLen);
  size_t written = ModemSerial.write(data, dataLen);
  if (written != dataLen) {
    DebugSerial.print("MODEM binary write short: ");
    DebugSerial.print(written);
    DebugSerial.print('/');
    DebugSerial.println(dataLen);
    return false;
  }
  return modemWaitFor(okTimeoutMs, "OK");
}

bool initCellularNetwork() {
  ModemSerial.begin(MODEM_BAUD, SERIAL_8N1, MODEM_RX_PIN, MODEM_TX_PIN);
  delay(1200);
  modemDrain(500);

  bool atOk = false;
  for (int i = 0; i < 8; i++) {
    modemSendLine("AT");
    if (modemWaitFor(1500, "OK")) {
      atOk = true;
      break;
    }
    delay(800);
  }

  if (!atOk) {
    DebugSerial.println("A7670E AT check failed");
    return false;
  }

  modemCommandOk("ATE0", 2000);
  modemCommandOk("AT+CMEE=2", 2000);
  modemCommandOk("AT+CNMP=38", 5000);  // LTE only; the China Telecom SIM registers reliably this way.
  modemCommandOk("AT+COPS=0", 10000);
  modemCommandOk("AT+CPIN?", 3000);
  modemCommandOk("AT+CREG=2", 3000);
  modemCommandOk("AT+CEREG=2", 3000);
  modemCommandOk("AT+CGREG=2", 3000);

  String apnCmd = "AT+CGDCONT=1,\"IP\",\"";
  apnCmd += CELLULAR_APN;
  apnCmd += "\"";
  modemCommandOk(apnCmd, 5000);

  modemCommandOk("AT+CSOCKSETPN=1", 5000);

  bool registered = false;
  for (uint8_t i = 0; i < 45; i++) {
    modemDrain(20);
    modemSendLine("AT+CEREG?");
    if (modemWaitFor(3000, "+CEREG: 2,1", "+CEREG: 0,1", "+CEREG: 2,5", "+CEREG: 0,5")) {
      registered = true;
      break;
    }
    modemDrain(20);
    modemSendLine("AT+CGREG?");
    if (modemWaitFor(3000, "+CGREG: 2,1", "+CGREG: 0,1", "+CGREG: 2,5", "+CGREG: 0,5")) {
      registered = true;
      break;
    }
    if ((i % 5) == 0) {
      modemCommandOk("AT+CSQ", 3000);
      modemCommandOk("AT+CPSI?", 3000);
    }
    DebugSerial.println("Cellular network not registered; waiting");
    delay(2000);
  }

  if (!registered) {
    DebugSerial.println("Cellular network registration failed");
    return false;
  }

  bool attached = false;
  for (uint8_t i = 0; i < 8; i++) {
    modemCommandOk("AT+CGATT=1", 15000);
    modemDrain(20);
    modemSendLine("AT+CGATT?");
    if (modemWaitFor(3000, "+CGATT: 1")) {
      attached = true;
      break;
    }
    if ((i % 4) == 0) {
      modemCommandOk("AT+CSQ", 3000);
      modemCommandOk("AT+CPSI?", 3000);
      modemCommandOk("AT+CEREG?", 3000);
      modemCommandOk("AT+CGREG?", 3000);
    }
    DebugSerial.println("Cellular packet service not attached; retrying");
    delay(2000);
  }

  if (!attached) {
    DebugSerial.println("Cellular packet service attach failed");
    return false;
  }

  bool pdpActive = false;
  for (uint8_t i = 0; i < 4; i++) {
    modemCommandOk("AT+CGACT=1,1", 15000);
    modemDrain(20);
    modemSendLine("AT+CGACT?");
    if (modemWaitFor(5000, "+CGACT: 1,1")) {
      pdpActive = true;
      break;
    }
    DebugSerial.println("Cellular PDP context inactive; retrying");
    delay(3000);
  }

  if (!pdpActive) {
    DebugSerial.println("Cellular PDP context activation failed; trying modem data service anyway");
  }

  modemCommandOk("AT+NETOPEN", 20000);
  modemCommandOk("AT+IPADDR", 5000);
  modemCommandOk("AT+CGPADDR=1", 5000);

  return true;
}

bool mqttSubscribeCommandTopic() {
  String cmd = "AT+CMQTTSUB=0,";
  cmd += strlen(MQTT_COMMAND_TOPIC);
  cmd += ",1";

  modemDrain(20);
  modemSendLine(cmd);
  if (!modemWaitFor(5000, ">")) {
    DebugSerial.println("MQTT command subscribe prompt failed");
    return false;
  }

  ModemSerial.print(MQTT_COMMAND_TOPIC);
  ModemSerial.print("\r\n");
  bool ok = modemWaitFor(15000, "+CMQTTSUB: 0,0");
  serviceModemAsyncFor(1200);
  DebugSerial.print("MQTT command topic subscribe ");
  DebugSerial.println(ok ? "OK" : "FAIL");
  return ok;
}

bool mqttConnect() {
  uint32_t now = millis();
  if (lastMqttConnectAttemptMs != 0 && now - lastMqttConnectAttemptMs < MQTT_RETRY_INTERVAL_MS) {
    return false;
  }
  lastMqttConnectAttemptMs = now;

  if (!modemReady) {
    modemReady = initCellularNetwork();
    if (!modemReady) {
      mqttConnected = false;
      return false;
    }
  }

  modemCommand("AT+CMQTTDISC=0,60", 3000);
  modemCommand("AT+CMQTTREL=0", 3000);
  modemCommand("AT+CMQTTSTOP", 8000);

  bool mqttStarted = false;
  for (int attempt = 0; attempt < 2 && !mqttStarted; attempt++) {
    modemDrain(500);
    modemSendLine("AT+CMQTTSTART");
    if (!modemWaitFor(10000, "OK")) {
      DebugSerial.println("MQTT start command rejected");
      modemCommand("AT+CMQTTSTOP", 8000);
      delay(1500);
      continue;
    }

    if (modemWaitFor(30000, "+CMQTTSTART: 0", "+CMQTTSTART: 23", "+CMQTTSTART: 1")) {
      mqttStarted = true;
      break;
    }

    DebugSerial.println("MQTT start failed; stopping service before retry");
    modemCommand("AT+CMQTTSTOP", 10000);
    delay(2000);
  }

  if (!mqttStarted) {
    mqttConnected = false;
    return false;
  }

  String accq = "AT+CMQTTACCQ=0,\"";
  accq += MQTT_CLIENT_ID;
  accq += "\"";
  if (!modemCommandOk(accq, 8000)) {
    DebugSerial.println("MQTT acquire client failed");
    mqttConnected = false;
    return false;
  }

  String connectCmd = "AT+CMQTTCONNECT=0,\"tcp://";
  connectCmd += MQTT_BROKER;
  connectCmd += ":";
  connectCmd += MQTT_PORT;
  connectCmd += "\",60,1,\"";
  connectCmd += MQTT_USERNAME;
  connectCmd += "\",\"";
  connectCmd += MQTT_PASSWORD;
  connectCmd += "\"";

  modemDrain(20);
  modemSendLine(connectCmd);
  if (!modemWaitFor(30000, "+CMQTTCONNECT: 0,0")) {
    DebugSerial.println("MQTT connect command failed");
    mqttConnected = false;
    return false;
  }

  mqttConnected = true;
  mqttCommandSubscribed = mqttSubscribeCommandTopic();
  DebugSerial.println("MQTT connected");
  return true;
}

bool mqttPublish(const String &payload) {
  if (!mqttConnected && !mqttConnect()) {
    return false;
  }

  String topicCmd = "AT+CMQTTTOPIC=0,";
  topicCmd += strlen(MQTT_TOPIC);
  if (!modemWaitPromptAndSend(topicCmd, MQTT_TOPIC, 5000, 5000)) {
    mqttConnected = false;
    return false;
  }

  String payloadCmd = "AT+CMQTTPAYLOAD=0,";
  payloadCmd += payload.length();
  if (!modemWaitPromptAndSend(payloadCmd, payload, 5000, 8000)) {
    mqttConnected = false;
    return false;
  }

  modemDrain(20);
  modemSendLine("AT+CMQTTPUB=0,0,60");
  if (!modemWaitFor(70000, "+CMQTTPUB: 0,0")) {
    mqttConnected = false;
    return false;
  }

  return true;
}

bool mqttPublishText(const char *topic, const String &payload, uint8_t qos = 1) {
  if (!topic) {
    return false;
  }

  if (!mqttConnected && !mqttConnect()) {
    return false;
  }

  String topicCmd = "AT+CMQTTTOPIC=0,";
  topicCmd += strlen(topic);
  if (!modemWaitPromptAndSend(topicCmd, String(topic), 5000, 5000)) {
    mqttConnected = false;
    return false;
  }

  String payloadCmd = "AT+CMQTTPAYLOAD=0,";
  payloadCmd += payload.length();
  if (!modemWaitPromptAndSend(payloadCmd, payload, 5000, 20000)) {
    mqttConnected = false;
    return false;
  }

  modemDrain(20);
  String pubCmd = "AT+CMQTTPUB=0,";
  pubCmd += String(qos ? 1 : 0);
  pubCmd += ",60";
  modemSendLine(pubCmd);
  if (!modemWaitFor(70000, "+CMQTTPUB: 0,0")) {
    mqttConnected = false;
    return false;
  }

  return true;
}

bool mqttPublishRaw(const char *topic, const uint8_t *payload, size_t payloadLen) {
  if (!topic || !payload || payloadLen == 0) {
    return false;
  }

  if (!mqttConnected && !mqttConnect()) {
    return false;
  }

  String topicCmd = "AT+CMQTTTOPIC=0,";
  topicCmd += strlen(topic);
  if (!modemWaitPromptAndSend(topicCmd, String(topic), 5000, 5000)) {
    mqttConnected = false;
    return false;
  }

  String payloadCmd = "AT+CMQTTPAYLOAD=0,";
  payloadCmd += payloadLen;
  if (!modemWaitPromptAndWrite(payloadCmd, payload, payloadLen, 5000, 10000)) {
    mqttConnected = false;
    return false;
  }

  modemDrain(20);
  modemSendLine("AT+CMQTTPUB=0,0,60");
  if (!modemWaitFor(70000, "+CMQTTPUB: 0,0")) {
    mqttConnected = false;
    return false;
  }

  return true;
}

void appendFloatArray(String &out, const float *values, int count, int precision) {
  out += '[';
  for (int i = 0; i < count; i++) {
    if (i > 0) out += ',';
    out += String(values[i], precision);
  }
  out += ']';
}

bool batteryReadRegister16(uint8_t reg, uint16_t &value) {
  BatteryWire.beginTransmission(BATTERY_I2C_ADDRESS);
  BatteryWire.write(reg);
  if (BatteryWire.endTransmission(false) != 0) {
    return false;
  }

  if (BatteryWire.requestFrom((int)BATTERY_I2C_ADDRESS, 2) != 2) {
    return false;
  }

  value = ((uint16_t)BatteryWire.read() << 8) | BatteryWire.read();
  return true;
}

bool initBatteryGauge() {
  BatteryWire.begin(BATTERY_SDA_PIN, BATTERY_SCL_PIN, BATTERY_I2C_FREQ);
  delay(20);

  uint16_t rawVcell = 0;
  batteryData.found = batteryReadRegister16(0x02, rawVcell);
  batteryData.valid = false;
  batteryData.rawVcell = rawVcell;
  batteryData.rawSoc = 0;
  batteryData.voltage = 0.0f;
  batteryData.percent = 0.0f;
  batteryData.lastReadMs = 0;
  return batteryData.found;
}

bool updateBatteryReading(bool force = false) {
  uint32_t now = millis();
  if (!force && batteryData.lastReadMs != 0 && now - batteryData.lastReadMs < BATTERY_READ_INTERVAL_MS) {
    return batteryData.valid;
  }

  if (!batteryData.found) {
    batteryData.valid = false;
    return false;
  }

  uint16_t rawVcell = 0;
  uint16_t rawSoc = 0;
  bool ok = batteryReadRegister16(0x02, rawVcell) && batteryReadRegister16(0x04, rawSoc);
  batteryData.lastReadMs = now;
  batteryData.valid = ok;
  if (!ok) {
    return false;
  }

  batteryData.rawVcell = rawVcell;
  batteryData.rawSoc = rawSoc;
  batteryData.voltage = (float)(rawVcell >> 4) * 0.00125f;
  batteryData.percent = constrain((float)rawSoc / 256.0f, 0.0f, 100.0f);
  return true;
}

void logBatteryReading() {
  if (millis() - lastBatteryLogMs < 5000) {
    return;
  }
  lastBatteryLogMs = millis();

  updateBatteryReading(true);
  if (batteryData.valid) {
    DebugSerial.printf("Battery MAX17048: %.3f V, %.1f%%, rawVcell=0x%04X, rawSoc=0x%04X\n",
                       batteryData.voltage,
                       batteryData.percent,
                       batteryData.rawVcell,
                       batteryData.rawSoc);
  } else {
    DebugSerial.printf("Battery MAX17048: not available on SDA=%d SCL=%d\n",
                       BATTERY_SDA_PIN,
                       BATTERY_SCL_PIN);
  }
}

String buildCloudPayload() {
  serviceHealthModule();
  updateBatteryReading();

  size_t sampleCount = 0;
  bool audioOk = false;
  if (AUDIO_STREAM_ENABLED || AUDIO_FILE_ENABLED) {
    audioOk = audioStreamStatsReady;
  } else {
    audioOk = readMicFrame(sampleCount) &&
              computeAudioFeatures(micSamples, (int)sampleCount, latestAudioRms, latestAudioZcr, audioMel);
    if (audioOk) {
      audioFrameIndex++;
    }
  }

  bool imuOk = IMU_I2C_ReadAllFlexible(&imuData);
  serviceHealthModule();

  String payload;
  payload.reserve(3800);

  payload += "{\"deviceId\":\"";
  payload += DEVICE_ID;
  payload += "\",\"ts_ms\":";
  payload += millis();
  payload += ",\"experiment\":{\"active\":";
  payload += (experimentActive && sdLoggingActive) ? "true" : "false";
  payload += ",\"id\":\"";
  payload += experimentId;
  payload += "\",\"sdLogging\":";
  payload += sdLoggingActive ? "true" : "false";
  payload += ",\"sdAudioBytes\":";
  payload += 0;
  payload += ",\"sdRows\":";
  payload += sdRowsWritten;
  payload += ",\"sdAudioMode\":\"features_only\"";
  payload += ",\"sdStatus\":\"";
  payload += sdStatusMessage;
  payload += "\",\"eventActive\":";
  payload += sdEventActive ? "true" : "false";
  payload += ",\"eventId\":\"";
  payload += sdEventId;
  payload += "\",\"eventLabel\":\"";
  payload += sdEventLabel;
  payload += "\",\"eventSegment\":\"";
  payload += sdEventSegmentId;
  payload += "\",\"eventAudioBytes\":";
  payload += 0;
  payload += "}";

  payload += ",\"battery\":{\"ok\":";
  payload += batteryData.valid ? "true" : "false";
  payload += ",\"found\":";
  payload += batteryData.found ? "true" : "false";
  payload += ",\"source\":\"MAX17048\"";
  payload += ",\"sda\":";
  payload += BATTERY_SDA_PIN;
  payload += ",\"scl\":";
  payload += BATTERY_SCL_PIN;
  payload += ",\"voltage\":";
  payload += String(batteryData.valid ? batteryData.voltage : 0.0f, 3);
  payload += ",\"percent\":";
  payload += String(batteryData.valid ? batteryData.percent : 0.0f, 1);
  payload += ",\"rawVcell\":";
  payload += batteryData.rawVcell;
  payload += ",\"rawSoc\":";
  payload += batteryData.rawSoc;
  payload += '}';

  payload += ",\"audio\":{\"ok\":";
  payload += audioOk ? "true" : "false";
  payload += ",\"recording\":";
  payload += audioFileRecording ? "true" : "false";
  payload += ",\"uploading\":";
  payload += audioFileUploading ? "true" : "false";
  payload += ",\"recordBytes\":";
  payload += audioFileCaptureCount;
  payload += ",\"recordCapacityBytes\":";
  payload += audioFileCapacitySamples;
  payload += ",\"sampleRate\":";
  payload += AUDIO_FILE_SAMPLE_RATE;
  payload += ",\"bits\":";
  payload += AUDIO_FILE_BITS_PER_SAMPLE;
  payload += ",\"frame\":";
  payload += audioFrameIndex;
  payload += ",\"rms\":";
  payload += String(latestAudioRms, 6);
  payload += ",\"zcr\":";
  payload += String(latestAudioZcr, 6);
  payload += ",\"mel\":";
  appendFloatArray(payload, audioMel, AUDIO_MEL_BANDS, 4);
  payload += '}';

  payload += ",\"imu\":{\"ok\":";
  payload += imuOk ? "true" : "false";
  payload += ",\"accel\":";
  appendFloatArray(payload, imuData.accel, 3, 6);
  payload += ",\"gyro\":";
  appendFloatArray(payload, imuData.gyro, 3, 6);
  payload += ",\"mag\":";
  appendFloatArray(payload, imuData.mag, 3, 6);
  payload += ",\"quat\":";
  appendFloatArray(payload, imuData.quat, 4, 6);
  payload += ",\"euler\":";
  appendFloatArray(payload, imuData.euler, 3, 3);
  payload += ",\"baro\":";
  appendFloatArray(payload, imuData.baro, 4, 6);
  payload += '}';

  payload += ",\"health\":{\"valid\":";
  payload += healthData.valid ? "true" : "false";
  payload += ",\"heartRate\":";
  payload += String(healthData.valid ? (int)healthData.heartRate : 0);
  payload += ",\"spo2\":";
  payload += String(healthData.valid ? (int)healthData.spo2 : 0);
  payload += ",\"microCir\":";
  payload += String(healthData.valid ? (int)healthData.microCir : 0);
  payload += ",\"systolic\":";
  payload += String(healthData.valid ? (int)healthData.systolic : 0);
  payload += ",\"diastolic\":";
  payload += String(healthData.valid ? (int)healthData.diastolic : 0);
  payload += ",\"respiration\":";
  payload += String(healthData.valid ? (int)healthData.respiration : 0);
  payload += ",\"fatigue\":";
  payload += String(healthData.valid ? (int)healthData.fatigue : 0);
  payload += ",\"rrInterval\":";
  payload += String(healthData.valid ? (int)healthData.rrInterval : 0);
  payload += ",\"hrvSdnn\":";
  payload += String(healthData.valid ? (int)healthData.hrvSdnn : 0);
  payload += ",\"hrvRmssd\":";
  payload += String(healthData.valid ? (int)healthData.hrvRmssd : 0);
  payload += ",\"bodyTemp\":";
  payload += String(healthData.valid ? healthData.bodyTemp : 0.0f, 1);
  payload += ",\"envTemp\":";
  payload += String(healthData.valid ? healthData.envTemp : 0.0f, 1);
  payload += "}}";

  return payload;
}

static void beginCloudBatch() {
  cloudBatchPayload = "";
  cloudBatchPayload.reserve(12000);
  cloudBatchPayload += "{\"type\":\"sensor_batch\",\"deviceId\":\"";
  cloudBatchPayload += DEVICE_ID;
  cloudBatchPayload += "\",\"intervalMs\":";
  cloudBatchPayload += CLOUD_SAMPLE_INTERVAL_MS;
  cloudBatchPayload += ",\"samples\":[";
  cloudBatchCount = 0;
  cloudBatchStartedMs = millis();
}

static bool appendCloudBatchSample() {
  if (cloudBatchCount >= CLOUD_BATCH_MAX_FRAMES) {
    return false;
  }
  if (cloudBatchCount == 0) {
    beginCloudBatch();
  } else {
    cloudBatchPayload += ',';
  }

  serviceHealthModule();
  updateBatteryReading();

  size_t sampleCount = 0;
  bool audioOk = false;
  if (AUDIO_STREAM_ENABLED || AUDIO_FILE_ENABLED) {
    audioOk = audioStreamStatsReady;
  } else {
    audioOk = readMicFrame(sampleCount) &&
              computeAudioFeatures(micSamples, (int)sampleCount, latestAudioRms, latestAudioZcr, audioMel);
    if (audioOk) {
      audioFrameIndex++;
    }
  }

  bool imuOk = IMU_I2C_ReadAllFlexible(&imuData);
  serviceHealthModule();

  cloudBatchPayload += "{\"t\":";
  cloudBatchPayload += millis();

  cloudBatchPayload += ",\"e\":[";
  cloudBatchPayload += (experimentActive && sdLoggingActive) ? "true" : "false";
  cloudBatchPayload += ",\"";
  cloudBatchPayload += experimentId;
  cloudBatchPayload += "\",";
  cloudBatchPayload += sdLoggingActive ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += sdRowsWritten;
  cloudBatchPayload += ",\"";
  cloudBatchPayload += sdStatusMessage;
  cloudBatchPayload += "\",";
  cloudBatchPayload += sdEventActive ? "true" : "false";
  cloudBatchPayload += ",\"";
  cloudBatchPayload += sdEventId;
  cloudBatchPayload += "\",\"";
  cloudBatchPayload += sdEventLabel;
  cloudBatchPayload += "\",\"";
  cloudBatchPayload += sdEventSegmentId;
  cloudBatchPayload += "\",0]";

  cloudBatchPayload += ",\"b\":[";
  cloudBatchPayload += batteryData.valid ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += batteryData.found ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += String(batteryData.valid ? batteryData.voltage : 0.0f, 3);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(batteryData.valid ? batteryData.percent : 0.0f, 1);
  cloudBatchPayload += ',';
  cloudBatchPayload += batteryData.rawVcell;
  cloudBatchPayload += ',';
  cloudBatchPayload += batteryData.rawSoc;
  cloudBatchPayload += ']';

  cloudBatchPayload += ",\"a\":[";
  cloudBatchPayload += audioOk ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += audioFileRecording ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += audioFileUploading ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += audioFileCaptureCount;
  cloudBatchPayload += ',';
  cloudBatchPayload += audioFileCapacitySamples;
  cloudBatchPayload += ',';
  cloudBatchPayload += AUDIO_FILE_SAMPLE_RATE;
  cloudBatchPayload += ',';
  cloudBatchPayload += AUDIO_FILE_BITS_PER_SAMPLE;
  cloudBatchPayload += ',';
  cloudBatchPayload += audioFrameIndex;
  cloudBatchPayload += ',';
  cloudBatchPayload += String(latestAudioRms, 6);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(latestAudioZcr, 6);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, audioMel, AUDIO_MEL_BANDS, 4);
  cloudBatchPayload += ']';

  cloudBatchPayload += ",\"i\":[";
  cloudBatchPayload += imuOk ? "true" : "false";
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.accel, 3, 6);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.gyro, 3, 6);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.mag, 3, 6);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.quat, 4, 6);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.euler, 3, 3);
  cloudBatchPayload += ',';
  appendFloatArray(cloudBatchPayload, imuData.baro, 4, 6);
  cloudBatchPayload += ']';

  cloudBatchPayload += ",\"h\":[";
  cloudBatchPayload += healthData.valid ? "true" : "false";
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.heartRate : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.spo2 : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.microCir : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.systolic : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.diastolic : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.respiration : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.fatigue : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.rrInterval : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.hrvSdnn : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? (int)healthData.hrvRmssd : 0);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? healthData.bodyTemp : 0.0f, 1);
  cloudBatchPayload += ',';
  cloudBatchPayload += String(healthData.valid ? healthData.envTemp : 0.0f, 1);
  cloudBatchPayload += "]}";

  cloudBatchCount++;
  return true;
}

void publishCloudSnapshot() {
  if (cloudBatchCount == 0) {
    appendCloudBatchSample();
  }
  if (cloudBatchCount == 0) {
    return;
  }

  cloudBatchPayload += "],\"count\":";
  cloudBatchPayload += cloudBatchCount;
  cloudBatchPayload += '}';

  DebugSerial.print("MQTT batch bytes=");
  DebugSerial.print(cloudBatchPayload.length());
  DebugSerial.print(" frames=");
  DebugSerial.println(cloudBatchCount);

  if (mqttPublish(cloudBatchPayload)) {
    DebugSerial.println("MQTT batch publish OK");
  } else {
    if (millis() - lastMqttFailureLogMs >= 5000) {
      lastMqttFailureLogMs = millis();
      DebugSerial.println("MQTT batch publish failed; will retry after backoff");
    }
    mqttConnected = false;
  }
  cloudBatchPayload = "";
  cloudBatchCount = 0;
}

// =====================================================
// I2C 通用读写
// =====================================================
bool imuWriteBytes(uint8_t reg, const uint8_t *data, uint8_t len) {
  Wire.beginTransmission(IMU_I2C_ADDRESS);
  Wire.write(reg);
  for (uint8_t i = 0; i < len; i++) {
    Wire.write(data[i]);
  }
  return Wire.endTransmission() == 0;
}

bool imuReadBytes(uint8_t reg, uint8_t *buf, uint8_t len) {
  Wire.beginTransmission(IMU_I2C_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t got = Wire.requestFrom((int)IMU_I2C_ADDRESS, (int)len);
  if (got != len) {
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    buf[i] = Wire.read();
  }
  return true;
}

// =====================================================
// 麦克风
// =====================================================
bool initMicrophone() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
    .sample_rate = MIC_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 256,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_PIN_NO_CHANGE,
    .ws_io_num = MIC_CLK_PIN,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = MIC_DATA_PIN
  };

  esp_err_t err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  if (err != ESP_OK) {
    DebugSerial.printf("Mic i2s_driver_install failed: %d\n", err);
    return false;
  }

  err = i2s_set_pin(I2S_PORT, &pin_config);
  if (err != ESP_OK) {
    DebugSerial.printf("Mic i2s_set_pin failed: %d\n", err);
    return false;
  }

  err = i2s_set_clk(I2S_PORT, MIC_SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  if (err != ESP_OK) {
    DebugSerial.printf("Mic i2s_set_clk failed: %d\n", err);
    return false;
  }

  DebugSerial.println("Mic init OK");
  return true;
}

bool readMicOneSample(int16_t &sampleOut, int16_t &envelopeOut) {
  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, micSamples, sizeof(micSamples), &bytesRead, 10);

  if (err != ESP_OK || bytesRead < 2) {
    return false;
  }

  int n = bytesRead / sizeof(int16_t);
  sampleOut = micSamples[n - 1];

  long sumAbs = 0;
  for (int i = 0; i < n; i++) {
    sumAbs += abs(micSamples[i]);
  }
  envelopeOut = (int16_t)(sumAbs / n);

  return true;
}

static float hzToMel(float hz) {
  return 2595.0f * log10f(1.0f + hz / 700.0f);
}

static float melToHz(float mel) {
  return 700.0f * (powf(10.0f, mel / 2595.0f) - 1.0f);
}

void initAudioFeatureExtractor() {
  memset(audioMelFilters, 0, sizeof(audioMelFilters));

  const float twoPi = 6.28318530718f;
  for (int i = 0; i < I2S_READ_LEN; i++) {
    audioWindow[i] = 0.5f - 0.5f * cosf(twoPi * i / (I2S_READ_LEN - 1));
  }

  const float minMel = hzToMel(AUDIO_MIN_HZ);
  const float maxMel = hzToMel(MIC_SAMPLE_RATE / 2.0f);
  float melPoints[AUDIO_MEL_BANDS + 2];
  float hzPoints[AUDIO_MEL_BANDS + 2];
  int binPoints[AUDIO_MEL_BANDS + 2];

  for (int i = 0; i < AUDIO_MEL_BANDS + 2; i++) {
    melPoints[i] = minMel + (maxMel - minMel) * i / (AUDIO_MEL_BANDS + 1);
    hzPoints[i] = melToHz(melPoints[i]);
    binPoints[i] = constrain(
      (int)floorf((I2S_READ_LEN + 1) * hzPoints[i] / MIC_SAMPLE_RATE),
      0,
      AUDIO_FFT_BINS - 1
    );
  }

  for (int m = 0; m < AUDIO_MEL_BANDS; m++) {
    int left = binPoints[m];
    int center = binPoints[m + 1];
    int right = binPoints[m + 2];

    if (center <= left) center = left + 1;
    if (right <= center) right = center + 1;
    if (center >= AUDIO_FFT_BINS) center = AUDIO_FFT_BINS - 2;
    if (right >= AUDIO_FFT_BINS) right = AUDIO_FFT_BINS - 1;
    if (center <= left || right <= center) {
      continue;
    }

    for (int k = left; k < center && k < AUDIO_FFT_BINS; k++) {
      audioMelFilters[m][k] = (float)(k - left) / (float)(center - left);
    }
    for (int k = center; k <= right && k < AUDIO_FFT_BINS; k++) {
      audioMelFilters[m][k] = (float)(right - k) / (float)(right - center);
    }
  }

  audioFeatureReady = true;
  DebugSerial.println("Audio feature extractor init OK");
}

bool readMicFrame(size_t &sampleCountOut) {
  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, micSamples, sizeof(micSamples), &bytesRead, 20);
  if (err != ESP_OK || bytesRead < sizeof(micSamples)) {
    sampleCountOut = 0;
    return false;
  }

  sampleCountOut = I2S_READ_LEN;
  return true;
}

static void putU16LE(uint8_t *dst, uint16_t value) {
  dst[0] = (uint8_t)(value & 0xFF);
  dst[1] = (uint8_t)((value >> 8) & 0xFF);
}

static void putU32LE(uint8_t *dst, uint32_t value) {
  dst[0] = (uint8_t)(value & 0xFF);
  dst[1] = (uint8_t)((value >> 8) & 0xFF);
  dst[2] = (uint8_t)((value >> 16) & 0xFF);
  dst[3] = (uint8_t)((value >> 24) & 0xFF);
}

static void writeWavHeader(File &file, uint32_t dataBytes) {
  uint8_t header[44] = {0};
  const uint32_t riffSize = 36 + dataBytes;
  const uint16_t audioFormat = 1;
  const uint16_t channels = 1;
  const uint32_t byteRate = MIC_SAMPLE_RATE * channels * sizeof(int16_t);
  const uint16_t blockAlign = channels * sizeof(int16_t);
  const uint16_t bitsPerSample = 16;

  memcpy(&header[0], "RIFF", 4);
  putU32LE(&header[4], riffSize);
  memcpy(&header[8], "WAVE", 4);
  memcpy(&header[12], "fmt ", 4);
  putU32LE(&header[16], 16);
  putU16LE(&header[20], audioFormat);
  putU16LE(&header[22], channels);
  putU32LE(&header[24], MIC_SAMPLE_RATE);
  putU32LE(&header[28], byteRate);
  putU16LE(&header[32], blockAlign);
  putU16LE(&header[34], bitsPerSample);
  memcpy(&header[36], "data", 4);
  putU32LE(&header[40], dataBytes);

  file.seek(0);
  file.write(header, sizeof(header));
}

static void csvFloat(File &file, float value, uint8_t decimals) {
  if (isfinite(value)) {
    file.print(value, decimals);
  }
}

static bool createSdRunDir() {
  sdRunDir = "";
  sdRunId = experimentId.length() ? experimentId : String("run_") + String(millis());
  String clean = "";
  for (uint16_t i = 0; i < sdRunId.length() && clean.length() < 48; i++) {
    char ch = sdRunId[i];
    if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_') {
      clean += ch;
    } else {
      clean += '_';
    }
  }
  if (clean.length() == 0) {
    clean = String("run_") + String(millis());
  }
  sdRunId = clean;
  sdFilePrefix = "petsense_";
  sdFilePrefix += sdRunId;
  sdFilePrefix += "_";
  sdStatusMessage = "using root directory";
  DebugSerial.print("SD using root directory with file prefix ");
  DebugSerial.println(sdFilePrefix);
  return true;
}

static String sdLogPath(const char *filename) {
  if (sdRunDir.length() == 0) {
    String path = "/";
    path += sdFilePrefix;
    path += filename;
    return path;
  }
  String path = sdRunDir + "/";
  path += filename;
  return path;
}

static String sanitizePathToken(const String &value, uint8_t maxLen) {
  String clean = "";
  for (uint16_t i = 0; i < value.length() && clean.length() < maxLen; i++) {
    char ch = value[i];
    if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_') {
      clean += ch;
    } else {
      clean += '_';
    }
  }
  return clean.length() ? clean : String("unknown");
}

static String eventLabelForCsv(const String &eventId) {
  if (eventId == "01_standing") return "standing";
  if (eventId == "02_sitting") return "sitting";
  if (eventId == "03_resting") return "resting";
  if (eventId == "04_sleeping") return "sleeping";
  if (eventId == "05_walking") return "walking";
  if (eventId == "06_running") return "running";
  if (eventId == "07_eating") return "eating";
  if (eventId == "08_rolling") return "rolling";
  if (eventId == "09_toilet") return "toilet";
  if (eventId == "10_jumping") return "jumping";
  return eventId;
}

static String sdEventPath(const char *filename) {
  if (sdEventDir.length()) {
    String path = sdEventDir + "/";
    path += filename;
    return path;
  }
  String path = "/";
  path += sdEventFilePrefix;
  path += filename;
  return path;
}

static String sdEventDirectoryPath(const String &eventDirName) {
  if (sdRunDir.length()) {
    return sdRunDir + "/" + eventDirName;
  }
  String path = "/";
  path += sdFilePrefix;
  path += eventDirName;
  return path;
}

static void ensureSdEventDirectories() {
  for (uint8_t i = 0; i < EVENT_DIR_COUNT; i++) {
    String dir = sdEventDirectoryPath(EVENT_DIR_IDS[i]);
    if (!SD_MMC.exists(dir.c_str())) {
      SD_MMC.mkdir(dir.c_str());
    }
  }
}

static void syncWavSegment(File &file, uint32_t dataBytes) {
  if (!file) {
    return;
  }

  writeWavHeader(file, dataBytes);
  file.flush();
  file.seek(44 + dataBytes);
}

static String audioSegmentFileName(uint32_t index, uint32_t ts) {
  String name = "audio_seg_";
  name += String(index);
  name += "_";
  name += String(ts);
  name += ".wav";
  return name;
}

static void closeMainAudioSegment() {
  if (!sdAudioFile) {
    return;
  }

  syncWavSegment(sdAudioFile, sdAudioSegmentBytes);
  sdAudioFile.close();
  sdAudioSegmentBytes = 0;
  sdAudioSegmentStartMs = 0;
}

static bool openMainAudioSegment(uint32_t now) {
  if (sdAudioFile) {
    closeMainAudioSegment();
  }

  String name = audioSegmentFileName(sdAudioSegmentIndex++, now);
  sdAudioFile = SD_MMC.open(sdLogPath(name.c_str()), FILE_WRITE);
  if (!sdAudioFile) {
    DebugSerial.println("SD audio segment open failed");
    sdStatusMessage = "audio segment open failed";
    return false;
  }

  sdAudioSegmentBytes = 0;
  sdAudioSegmentStartMs = now;
  syncWavSegment(sdAudioFile, 0);
  return true;
}

static bool rotateMainAudioSegmentIfNeeded(uint32_t now) {
  if (sdAudioFile && now - sdAudioSegmentStartMs < SD_AUDIO_SEGMENT_MS) {
    return true;
  }
  if (!sdAudioFile && now - sdLastAudioSegmentRetryMs < SD_AUDIO_SEGMENT_RETRY_MS) {
    return false;
  }
  sdLastAudioSegmentRetryMs = now;
  return openMainAudioSegment(now);
}

static void closeEventAudioSegment() {
  if (!sdEventAudioFile) {
    return;
  }

  syncWavSegment(sdEventAudioFile, sdEventAudioSegmentBytes);
  sdEventAudioFile.close();
  sdEventAudioSegmentBytes = 0;
  sdEventAudioSegmentStartMs = 0;
}

static bool openEventAudioSegment(uint32_t now) {
  if (sdEventAudioFile) {
    closeEventAudioSegment();
  }

  String name = sdEventSegmentId + "_audio_seg_" + String(sdEventAudioSegmentIndex++) + "_" + String(now) + ".wav";
  sdEventAudioFile = SD_MMC.open(sdEventPath(name.c_str()), FILE_WRITE);
  if (!sdEventAudioFile) {
    DebugSerial.println("SD event audio segment open failed");
    return false;
  }

  sdEventAudioSegmentBytes = 0;
  sdEventAudioSegmentStartMs = now;
  syncWavSegment(sdEventAudioFile, 0);
  return true;
}

static bool rotateEventAudioSegmentIfNeeded(uint32_t now) {
  if (!sdEventActive) {
    return true;
  }
  if (sdEventAudioFile && now - sdEventAudioSegmentStartMs < SD_AUDIO_SEGMENT_MS) {
    return true;
  }
  if (!sdEventAudioFile && now - sdLastEventAudioSegmentRetryMs < SD_AUDIO_SEGMENT_RETRY_MS) {
    return false;
  }
  sdLastEventAudioSegmentRetryMs = now;
  return openEventAudioSegment(now);
}

static void writeSdManifest(bool micOK, bool audioBufferOK, bool imuOK, bool batteryOK) {
  String path = sdLogPath("manifest.json");
  File manifest = SD_MMC.open(path, FILE_WRITE);
  if (!manifest) {
    DebugSerial.println("SD manifest open failed");
    return;
  }

  manifest.println("{");
  manifest.println("  \"schema_version\": 1,");
  manifest.print("  \"device_id\": \""); manifest.print(DEVICE_ID); manifest.println("\",");
  manifest.print("  \"experiment_id\": \""); manifest.print(sdRunId); manifest.println("\",");
  manifest.print("  \"start_ms\": "); manifest.print(sdLogStartMs); manifest.println(",");
  manifest.print("  \"target_duration_ms\": "); manifest.print(SD_LOG_DURATION_MS); manifest.println(",");
  manifest.print("  \"run_dir\": \""); manifest.print(sdRunDir.length() ? sdRunDir : "/"); manifest.println("\",");
  manifest.print("  \"file_prefix\": \""); manifest.print(sdFilePrefix); manifest.println("\",");
  manifest.println("  \"files\": {");
  manifest.println("    \"local_raw_audio\": {\"enabled\":false,\"reason\":\"feature_only_sd_capture\"},");
  manifest.println("    \"samples\": {\"path\":\"samples.csv\",\"fast_rate_hz\":10,\"slow_rate_hz\":1,\"columns\":\"ts_ms,experiment_id,event_active,event_id,event_label,event_segment,audio_features,imu,health_fresh,health,battery_fresh,battery\"}");
  manifest.println("  },");
  manifest.println("  \"pins\": {");
  manifest.print("    \"mic_clk\": "); manifest.print(MIC_CLK_PIN); manifest.println(",");
  manifest.print("    \"mic_data\": "); manifest.print(MIC_DATA_PIN); manifest.println(",");
  manifest.print("    \"imu_sda\": "); manifest.print(IMU_SDA_PIN); manifest.println(",");
  manifest.print("    \"imu_scl\": "); manifest.print(IMU_SCL_PIN); manifest.println(",");
  manifest.print("    \"health_rx\": "); manifest.print(HEALTH_RX_PIN); manifest.println(",");
  manifest.print("    \"health_tx\": "); manifest.print(HEALTH_TX_PIN); manifest.println(",");
  manifest.print("    \"sd_clk\": "); manifest.print(SDMMC_CLK_PIN); manifest.println(",");
  manifest.print("    \"sd_cmd\": "); manifest.print(SDMMC_CMD_PIN); manifest.println(",");
  manifest.print("    \"sd_d0\": "); manifest.println(SDMMC_D0_PIN);
  manifest.println("  },");
  manifest.println("  \"init_status\": {");
  manifest.print("    \"mic_ok\": "); manifest.print(micOK ? "true" : "false"); manifest.println(",");
  manifest.print("    \"audio_buffer_ok\": "); manifest.print(audioBufferOK ? "true" : "false"); manifest.println(",");
  manifest.print("    \"imu_ok\": "); manifest.print(imuOK ? "true" : "false"); manifest.println(",");
  manifest.print("    \"battery_ok\": "); manifest.println(batteryOK ? "true" : "false");
  manifest.println("  }");
  manifest.println("}");
  manifest.close();
}

static void writeAudioFeatureHeader(File &file) {
  file.print("ts_ms,frame,rms,zcr");
  for (int i = 0; i < AUDIO_MEL_BANDS; i++) {
    file.print(",mel_");
    if (i < 10) file.print('0');
    file.print(i);
  }
  file.println();
}

static void writeImuHeader(File &file) {
  file.println("ts_ms,ok,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,mx,my,mz,qw,qx,qy,qz,roll_deg,pitch_deg,yaw_deg,baro0,baro1,baro2,baro3");
}

static void writeHealthHeader(File &file) {
  file.println("ts_ms,valid,heart_rate_bpm,spo2_pct,micro_circulation,systolic_mmhg,diastolic_mmhg,respiration_rpm,fatigue,rr_interval,hrv_sdnn,hrv_rmssd,body_temp_c,env_temp_c");
}

static void writeBatteryHeader(File &file) {
  file.println("ts_ms,ok,found,voltage_v,percent,raw_vcell,raw_soc");
}

static bool openSdLogFiles() {
  sdImuFile = SD_MMC.open(sdLogPath("samples.csv"), FILE_WRITE);

  if (!sdImuFile) {
    DebugSerial.println("SD samples.csv failed to open");
    sdStatusMessage = "log file open failed";
    return false;
  }

  sdImuFile.print("ts_ms,experiment_id,event_active,event_id,event_label,event_segment,");
  sdImuFile.print("audio_frame,audio_rms,audio_zcr");
  for (int i = 0; i < AUDIO_MEL_BANDS; i++) {
    sdImuFile.print(",mel_");
    if (i < 10) sdImuFile.print('0');
    sdImuFile.print(i);
  }
  sdImuFile.print(",imu_ok,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,mx,my,mz,qw,qx,qy,qz,roll_deg,pitch_deg,yaw_deg,baro0,baro1,baro2,baro3,");
  sdImuFile.print("health_fresh,health_valid,heart_rate_bpm,spo2_pct,micro_circulation,systolic_mmhg,diastolic_mmhg,respiration_rpm,fatigue,rr_interval,hrv_sdnn,hrv_rmssd,body_temp_c,env_temp_c,");
  sdImuFile.println("battery_fresh,battery_ok,battery_found,voltage_v,battery_percent,raw_vcell,raw_soc");
  sdImuFile.flush();
  return true;
}

bool initSdLogger(bool micOK, bool audioBufferOK, bool imuOK, bool batteryOK) {
  if (sdLoggingActive) {
    return true;
  }

  if (sdReady) {
    SD_MMC.end();
    sdReady = false;
    delay(100);
  }

  if (!sdReady) {
    pinMode(SDMMC_CD_PIN, INPUT_PULLUP);
    sdCardDetectAtInit = digitalRead(SDMMC_CD_PIN);
    bool pinsOk = SD_MMC.setPins(SDMMC_CLK_PIN, SDMMC_CMD_PIN, SDMMC_D0_PIN);
    DebugSerial.printf("SD setPins clk=%d cmd=%d d0=%d ok=%s cd=%d freq=%d kHz\n",
                       SDMMC_CLK_PIN,
                       SDMMC_CMD_PIN,
                       SDMMC_D0_PIN,
                       pinsOk ? "true" : "false",
                       sdCardDetectAtInit,
                       SDMMC_INIT_FREQ_KHZ);
    if (!pinsOk) {
      sdStatusMessage = "setPins failed";
      return false;
    }

    if (!SD_MMC.begin("/sdcard", true, false, SDMMC_INIT_FREQ_KHZ, SD_MAX_OPEN_FILES)) {
      DebugSerial.println("SD_MMC begin failed");
      sdStatusMessage = "SD_MMC begin failed";
      return false;
    }

    if (SD_MMC.cardType() == CARD_NONE) {
      DebugSerial.println("SD card not detected");
      sdStatusMessage = "card not detected";
      return false;
    }

    sdReady = true;
  }

  if (!createSdRunDir()) {
    return false;
  }

  sdLogStartMs = millis();
  sdAudioDataBytes = 0;
  sdAudioSegmentBytes = 0;
  sdAudioSegmentIndex = 0;
  sdAudioSegmentStartMs = 0;
  sdLastAudioSegmentRetryMs = 0;
  sdLastSensorLogMs = 0;
  sdLastHealthLogMs = 0;
  sdLastFlushMs = millis();
  sdRowsWritten = 0;

  if (!openSdLogFiles()) {
    stopSdLogger();
    return false;
  }

  (void)micOK;
  (void)audioBufferOK;
  (void)imuOK;
  (void)batteryOK;
  sdLoggingActive = true;
  sdLoggingComplete = false;
  sdStatusMessage = "features_only_logging";

  DebugSerial.print("SD logging started: ");
  DebugSerial.println(sdRunDir);
  DebugSerial.println("SD files: samples.csv only (raw WAV disabled; audio features only)");
  return true;
}

static void writeAudioFeatureRow(File &file, uint32_t ts) {
  if (!file) {
    return;
  }

  file.print(ts);
  file.print(',');
  file.print(audioFrameIndex);
  file.print(',');
  file.print(latestAudioRms, 6);
  file.print(',');
  file.print(latestAudioZcr, 6);
  for (int i = 0; i < AUDIO_MEL_BANDS; i++) {
    file.print(',');
    file.print(audioMel[i], 6);
  }
  file.println();
}

static void logSdAudioFeatures(uint32_t ts) {
  writeAudioFeatureRow(sdAudioFeatureFile, ts);
  if (sdEventActive) {
    writeAudioFeatureRow(sdEventAudioFeatureFile, ts);
  }
}

static void writeImuRow(File &file, uint32_t ts, bool imuOk) {
  if (!file) {
    return;
  }

  file.print(ts);
  file.print(',');
  file.print(imuOk ? 1 : 0);

  for (uint8_t i = 0; i < 3; i++) { file.print(','); csvFloat(file, imuData.accel[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { file.print(','); csvFloat(file, imuData.gyro[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { file.print(','); csvFloat(file, imuData.mag[i], 6); }
  for (uint8_t i = 0; i < 4; i++) { file.print(','); csvFloat(file, imuData.quat[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { file.print(','); csvFloat(file, imuData.euler[i], 3); }
  for (uint8_t i = 0; i < 4; i++) { file.print(','); csvFloat(file, imuData.baro[i], 6); }
  file.println();
}

static void logSdImu(uint32_t ts) {
  bool imuOk = IMU_I2C_ReadAllFlexible(&imuData);
  writeImuRow(sdImuFile, ts, imuOk);
  if (sdEventActive) {
    writeImuRow(sdEventImuFile, ts, imuOk);
  }
}

static void writeHealthRow(File &file, uint32_t ts) {
  if (!file) {
    return;
  }

  file.print(ts);
  file.print(',');
  file.print(healthData.valid ? 1 : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.heartRate : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.spo2 : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.microCir : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.systolic : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.diastolic : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.respiration : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.fatigue : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.rrInterval : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.hrvSdnn : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.hrvRmssd : 0);
  file.print(',');
  file.print(healthData.valid ? healthData.bodyTemp : 0.0f, 1);
  file.print(',');
  file.println(healthData.valid ? healthData.envTemp : 0.0f, 1);
}

static void logSdHealth(uint32_t ts) {
  serviceHealthModule();
  writeHealthRow(sdHealthFile, ts);
  if (sdEventActive) {
    writeHealthRow(sdEventHealthFile, ts);
  }
}

static void writeBatteryRow(File &file, uint32_t ts) {
  if (!file) {
    return;
  }

  file.print(ts);
  file.print(',');
  file.print(batteryData.valid ? 1 : 0);
  file.print(',');
  file.print(batteryData.found ? 1 : 0);
  file.print(',');
  file.print(batteryData.valid ? batteryData.voltage : 0.0f, 3);
  file.print(',');
  file.print(batteryData.valid ? batteryData.percent : 0.0f, 1);
  file.print(',');
  file.print(batteryData.rawVcell);
  file.print(',');
  file.println(batteryData.rawSoc);
}

static void writeEmptyCsvFields(File &file, uint8_t count) {
  for (uint8_t i = 0; i < count; i++) {
    file.print(',');
  }
}

static void writeCombinedSampleRow(uint32_t ts) {
  if (!sdImuFile) {
    return;
  }

  bool imuOk = IMU_I2C_ReadAllFlexible(&imuData);
  serviceHealthModule();
  bool slowFresh = (sdLastHealthLogMs == 0 || ts - sdLastHealthLogMs >= SD_HEALTH_LOG_INTERVAL_MS);
  if (slowFresh) {
    sdLastHealthLogMs = ts;
    updateBatteryReading();
  }

  sdImuFile.print(ts);
  sdImuFile.print(',');
  sdImuFile.print(sdRunId);
  sdImuFile.print(',');
  sdImuFile.print(sdEventActive ? 1 : 0);
  sdImuFile.print(',');
  sdImuFile.print(sdEventActive ? sdEventId : "");
  sdImuFile.print(',');
  sdImuFile.print(sdEventActive ? sdEventLabel : "");
  sdImuFile.print(',');
  sdImuFile.print(sdEventActive ? sdEventSegmentId : "");
  sdImuFile.print(',');

  sdImuFile.print(audioFrameIndex);
  sdImuFile.print(',');
  sdImuFile.print(latestAudioRms, 6);
  sdImuFile.print(',');
  sdImuFile.print(latestAudioZcr, 6);
  for (int i = 0; i < AUDIO_MEL_BANDS; i++) {
    sdImuFile.print(',');
    sdImuFile.print(audioMel[i], 6);
  }

  sdImuFile.print(',');
  sdImuFile.print(imuOk ? 1 : 0);
  for (uint8_t i = 0; i < 3; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.accel[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.gyro[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.mag[i], 6); }
  for (uint8_t i = 0; i < 4; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.quat[i], 6); }
  for (uint8_t i = 0; i < 3; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.euler[i], 3); }
  for (uint8_t i = 0; i < 4; i++) { sdImuFile.print(','); csvFloat(sdImuFile, imuData.baro[i], 6); }

  sdImuFile.print(',');
  sdImuFile.print(slowFresh ? 1 : 0);
  if (slowFresh) {
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? 1 : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.heartRate : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.spo2 : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.microCir : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.systolic : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.diastolic : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.respiration : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.fatigue : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.rrInterval : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.hrvSdnn : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.hrvRmssd : 0);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.bodyTemp : 0.0f, 1);
    sdImuFile.print(',');
    sdImuFile.print(healthData.valid ? healthData.envTemp : 0.0f, 1);
  } else {
    writeEmptyCsvFields(sdImuFile, 13);
  }

  sdImuFile.print(',');
  sdImuFile.print(slowFresh ? 1 : 0);
  if (slowFresh) {
    sdImuFile.print(',');
    sdImuFile.print(batteryData.valid ? 1 : 0);
    sdImuFile.print(',');
    sdImuFile.print(batteryData.found ? 1 : 0);
    sdImuFile.print(',');
    sdImuFile.print(batteryData.valid ? batteryData.voltage : 0.0f, 3);
    sdImuFile.print(',');
    sdImuFile.print(batteryData.valid ? batteryData.percent : 0.0f, 1);
    sdImuFile.print(',');
    sdImuFile.print(batteryData.rawVcell);
    sdImuFile.print(',');
    sdImuFile.println(batteryData.rawSoc);
  } else {
    writeEmptyCsvFields(sdImuFile, 6);
    sdImuFile.println();
  }
  sdRowsWritten++;
}

static void logSdBattery(uint32_t ts) {
  updateBatteryReading();
  writeBatteryRow(sdBatteryFile, ts);
  if (sdEventActive) {
    writeBatteryRow(sdEventBatteryFile, ts);
  }
}

static void flushSdLogs() {
  if (sdImuFile) sdImuFile.flush();
}

static bool openSdEventFiles(const String &eventDirName) {
  sdEventSegmentId = String("seg_") + String(millis());
  sdEventDir = "";
  sdEventFilePrefix = "";
  sdEventAudioDataBytes = 0;
  DebugSerial.print("SD event marker active id=");
  DebugSerial.print(eventDirName);
  DebugSerial.print(" segment=");
  DebugSerial.println(sdEventSegmentId);
  return true;
}

void stopEventCapture() {
  if (!sdEventActive) {
    return;
  }

  DebugSerial.print("SD event stopped id=");
  DebugSerial.print(sdEventId);
  DebugSerial.print(" segment=");
  DebugSerial.print(sdEventSegmentId);
  DebugSerial.print(" audio_bytes=");
  DebugSerial.println(sdEventAudioDataBytes);

  sdEventActive = false;
  sdEventId = "";
  sdEventLabel = "";
  sdEventSegmentId = "";
  sdEventDir = "";
  sdEventFilePrefix = "";
  sdEventAudioDataBytes = 0;
  sdEventAudioSegmentBytes = 0;
  sdEventAudioSegmentStartMs = 0;
  sdEventAudioSegmentIndex = 0;
  sdLastEventAudioSegmentRetryMs = 0;
}

bool startEventCapture(const String &eventId, const String &label) {
  if (!sdLoggingActive) {
    DebugSerial.println("Event start ignored; experiment SD logging inactive");
    return false;
  }

  String cleanId = sanitizePathToken(eventId, 32);
  if (sdEventActive && sdEventId == cleanId) {
    stopEventCapture();
    return true;
  }

  if (sdEventActive) {
    stopEventCapture();
  }

  sdEventId = cleanId;
  sdEventLabel = eventLabelForCsv(cleanId);
  sdEventActive = true;
  if (!openSdEventFiles(cleanId)) {
    DebugSerial.println("SD event file setup incomplete; keeping event active");
  }

  DebugSerial.print("SD event started id=");
  DebugSerial.print(sdEventId);
  DebugSerial.print(" label=");
  DebugSerial.print(sdEventLabel);
  DebugSerial.print(" segment=");
  DebugSerial.println(sdEventSegmentId);
  return true;
}

void stopSdLogger() {
  stopEventCapture();

  closeMainAudioSegment();
  if (sdImuFile) sdImuFile.close();

  if (sdLoggingActive) {
    DebugSerial.print("SD logging complete: ");
    DebugSerial.print(sdRunDir);
    DebugSerial.print(" audio_bytes=");
    DebugSerial.println(sdAudioDataBytes);
  }

  sdLoggingActive = false;
  sdLoggingComplete = true;
  sdStatusMessage = "complete";
}

bool startExperimentCapture(const String &id) {
  if (sdLoggingActive) {
    DebugSerial.println("Experiment capture already active");
    return true;
  }

  experimentId = id;
  if (experimentId.length() == 0) {
    experimentId = String("exp-") + String(millis());
  }

  sdLoggingComplete = false;
  bool ok = initSdLogger(deviceMicOK, deviceAudioBufferOK, deviceImuOK, deviceBatteryOK);
  experimentActive = ok;
  DebugSerial.print("Experiment capture ");
  DebugSerial.println(ok ? "started" : "failed");
  return ok;
}

void stopExperimentCapture() {
  if (sdLoggingActive) {
    stopSdLogger();
  }
  experimentActive = false;
  DebugSerial.println("Experiment capture stopped");
}

void sdLogStep() {
  if (!sdLoggingActive) {
    uint32_t now = millis();
    if (!sdLoggingComplete && now - lastSdStatusLogMs >= SD_FLUSH_INTERVAL_MS) {
      lastSdStatusLogMs = now;
      DebugSerial.print("SD logging inactive: ");
      DebugSerial.print(sdStatusMessage);
      DebugSerial.print(" cd_init=");
      DebugSerial.println(sdCardDetectAtInit);
    }
    return;
  }

  uint32_t now = millis();
  if (SD_LOG_DURATION_MS > 0 && now - sdLogStartMs >= SD_LOG_DURATION_MS) {
    stopExperimentCapture();
    return;
  }
  if (SD_HARD_SAFETY_STOP_MS > 0 && now - sdLogStartMs >= SD_HARD_SAFETY_STOP_MS) {
    DebugSerial.println("SD hard safety stop reached");
    stopExperimentCapture();
    return;
  }

  serviceHealthModule();

  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, audioFileReadSamples, sizeof(audioFileReadSamples), &bytesRead, 120);
  if (err == ESP_OK && bytesRead >= sizeof(int16_t) * 2) {
    updateAudioStats(audioFileReadSamples, (int)(bytesRead / sizeof(int16_t)));
  } else {
    DebugSerial.printf("SD audio read failed: err=%d bytes=%u\n", err, (unsigned)bytesRead);
  }

  if (now - sdLastSensorLogMs >= SD_SENSOR_LOG_INTERVAL_MS) {
    sdLastSensorLogMs = now;
    writeCombinedSampleRow(now);
  }

  if (now - sdLastFlushMs >= SD_FLUSH_INTERVAL_MS) {
    sdLastFlushMs = now;
    flushSdLogs();
    DebugSerial.print("SD logging progress ms=");
    DebugSerial.print(now - sdLogStartMs);
    DebugSerial.print(" rows=");
    DebugSerial.print(sdRowsWritten);
    DebugSerial.print(" audio_bytes=");
    DebugSerial.println(sdAudioDataBytes);
  }
}

static void updateAudioStats(const int16_t *samples, int n) {
  if (!samples || n <= 1) {
    return;
  }

  int featureSamples = n < I2S_READ_LEN ? n : I2S_READ_LEN;
  if (featureSamples <= 1) {
    return;
  }

  if (computeAudioFeatures(samples, featureSamples, latestAudioRms, latestAudioZcr, audioMel)) {
    audioFrameIndex++;
    audioStreamStatsReady = true;
  }
}

void captureAudioFeatureStep() {
  if (!audioFeatureReady || AUDIO_STREAM_ENABLED || audioFileRecording) {
    return;
  }

  if (millis() - lastAudioFeatureMs < AUDIO_FEATURE_INTERVAL_MS) {
    return;
  }
  lastAudioFeatureMs = millis();

  size_t sampleCount = 0;
  if (readMicFrame(sampleCount)) {
    updateAudioStats(micSamples, (int)sampleCount);
  }
}

static uint8_t encodeImaNibble(int16_t sample, int &predictor, int &stepIndex) {
  int step = IMA_STEP_TABLE[stepIndex];
  int diff = sample - predictor;
  uint8_t code = 0;

  if (diff < 0) {
    code = 8;
    diff = -diff;
  }

  int diffq = step >> 3;
  if (diff >= step) {
    code |= 4;
    diff -= step;
    diffq += step;
  }
  if (diff >= (step >> 1)) {
    code |= 2;
    diff -= step >> 1;
    diffq += step >> 1;
  }
  if (diff >= (step >> 2)) {
    code |= 1;
    diffq += step >> 2;
  }

  if (code & 8) {
    predictor -= diffq;
  } else {
    predictor += diffq;
  }

  predictor = constrain(predictor, -32768, 32767);
  stepIndex = constrain(stepIndex + IMA_INDEX_TABLE[code], 0, 88);
  return code & 0x0F;
}

static size_t encodeImaAdpcmFrame(const int16_t *samples, uint16_t sampleCount, uint8_t *out, size_t outCapacity, int16_t &initialPredictor, uint8_t &initialStepIndex) {
  if (!samples || sampleCount == 0 || !out || outCapacity < 4) {
    return 0;
  }

  int predictor = samples[0];
  int stepIndex = 0;
  initialPredictor = (int16_t)predictor;
  initialStepIndex = (uint8_t)stepIndex;

  size_t outPos = 0;
  bool haveLowNibble = false;
  uint8_t packed = 0;

  for (uint16_t i = 1; i < sampleCount; i++) {
    uint8_t nibble = encodeImaNibble(samples[i], predictor, stepIndex);
    if (!haveLowNibble) {
      packed = nibble;
      haveLowNibble = true;
    } else {
      if (outPos >= outCapacity) {
        return 0;
      }
      out[outPos++] = packed | (nibble << 4);
      haveLowNibble = false;
    }
  }

  if (haveLowNibble) {
    if (outPos >= outCapacity) {
      return 0;
    }
    out[outPos++] = packed;
  }

  return outPos;
}

static void appendBase64(String &out, const uint8_t *data, size_t len) {
  static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

  for (size_t i = 0; i < len; i += 3) {
    uint32_t value = ((uint32_t)data[i]) << 16;
    bool hasB1 = (i + 1) < len;
    bool hasB2 = (i + 2) < len;
    if (hasB1) {
      value |= ((uint32_t)data[i + 1]) << 8;
    }
    if (hasB2) {
      value |= data[i + 2];
    }

    out += table[(value >> 18) & 0x3F];
    out += table[(value >> 12) & 0x3F];
    out += hasB1 ? table[(value >> 6) & 0x3F] : '=';
    out += hasB2 ? table[value & 0x3F] : '=';
  }
}

bool publishAudioStreamFrame() {
  if (!AUDIO_STREAM_ENABLED) {
    return true;
  }

  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, audioStreamSamples, sizeof(audioStreamSamples), &bytesRead, 300);
  if (err != ESP_OK || bytesRead < sizeof(int16_t) * 160) {
    DebugSerial.printf("Audio stream read failed: err=%d bytes=%u\n", err, (unsigned)bytesRead);
    return false;
  }

  uint16_t sampleCount = (uint16_t)(bytesRead / sizeof(int16_t));
  updateAudioStats(audioStreamSamples, sampleCount);

  uint8_t *header = audioStreamPayload;
  header[0] = 'P';
  header[1] = 'S';
  header[2] = 'A';
  header[3] = '2';
  putU32LE(header + 4, audioStreamSeq++);
  putU32LE(header + 8, millis());
  putU16LE(header + 12, MIC_SAMPLE_RATE);
  putU16LE(header + 14, sampleCount);
  header[16] = 1;   // mono
  header[17] = 4;   // IMA ADPCM bits per sample
  header[18] = 1;   // codec: 1 = IMA ADPCM
  header[19] = 0;

  int16_t initialPredictor = 0;
  uint8_t initialStepIndex = 0;
  uint8_t *adpcm = audioStreamPayload + AUDIO_STREAM_HEADER_LEN;
  size_t adpcmCapacity = sizeof(audioStreamPayload) - AUDIO_STREAM_HEADER_LEN;
  size_t adpcmBytes = encodeImaAdpcmFrame(audioStreamSamples, sampleCount, adpcm, adpcmCapacity, initialPredictor, initialStepIndex);
  if (adpcmBytes == 0) {
    DebugSerial.println("Audio ADPCM encode failed");
    return false;
  }

  putU16LE(header + 20, (uint16_t)initialPredictor);
  header[22] = initialStepIndex;
  header[23] = 0;

  String payload;
  payload.reserve(1280);
  payload += "{\"v\":1,\"codec\":\"ima-adpcm\",\"seq\":";
  payload += String(audioStreamSeq - 1);
  payload += ",\"ts_ms\":";
  payload += String(millis());
  payload += ",\"rate\":";
  payload += String(MIC_SAMPLE_RATE);
  payload += ",\"ch\":1,\"bits\":4,\"samples\":";
  payload += String(sampleCount);
  payload += ",\"predictor\":";
  payload += String(initialPredictor);
  payload += ",\"stepIndex\":";
  payload += String(initialStepIndex);
  payload += ",\"data\":\"";
  appendBase64(payload, adpcm, adpcmBytes);
  payload += "\"}";

  size_t payloadLen = payload.length();
  bool ok = mqttPublishText(MQTT_AUDIO_TOPIC, payload);

  if (millis() - lastAudioStreamLogMs >= AUDIO_STREAM_LOG_INTERVAL_MS) {
    lastAudioStreamLogMs = millis();
    DebugSerial.print("Audio MQTT ");
    DebugSerial.print(ok ? "OK" : "FAIL");
    DebugSerial.print(" seq=");
    DebugSerial.print(audioStreamSeq - 1);
    DebugSerial.print(" samples=");
    DebugSerial.print(sampleCount);
    DebugSerial.print(" bytes=");
    DebugSerial.println(payloadLen);
  }

  return ok;
}

static int16_t makePcm16Sample(int16_t sample) {
  int32_t amplified = (int32_t)sample * AUDIO_FILE_GAIN;
  amplified = constrain(amplified, -32768, 32767);
  return (int16_t)amplified;
}

bool initAudioFileBuffer() {
  audioFileCapacitySamples = AUDIO_FILE_MAX_BYTES;
  if (psramFound()) {
    audioFileSamples = (uint8_t *)ps_malloc(audioFileCapacitySamples);
  }

  if (!audioFileSamples) {
    audioFileCapacitySamples = AUDIO_FILE_BYTES;
    audioFileSamples = (uint8_t *)malloc(audioFileCapacitySamples);
  }

  if (!audioFileSamples) {
    audioFileCapacitySamples = 0;
    DebugSerial.println("Audio record buffer allocation failed");
    return false;
  }

  DebugSerial.print("Audio record buffer seconds=");
  DebugSerial.print(audioFileCapacitySamples / (AUDIO_FILE_SAMPLE_RATE * AUDIO_FILE_BYTES_PER_SAMPLE));
  DebugSerial.print(" bytes=");
  DebugSerial.println(audioFileCapacitySamples);
  return true;
}

void startAudioRecording() {
  if (!AUDIO_FILE_ENABLED || !audioFileSamples || audioFileUploading) {
    return;
  }

  audioFileCaptureCount = 0;
  audioFileUploadSamples = 0;
  audioFileReady = false;
  audioFileStartSent = false;
  audioFileSingleAttempted = false;
  audioFileUploadOffset = 0;
  audioFileUploadChunkIndex = 0;
  audioFileUploadTotalChunks = 0;
  audioFileAutoStopBytes = AUDIO_FILE_BYTES;
  audioFileRecording = true;
  DebugSerial.println("Audio recording started for 3 seconds at 16 kHz 16-bit");
}

void stopAudioRecording() {
  if (!AUDIO_FILE_ENABLED || !audioFileRecording) {
    return;
  }

  audioFileRecording = false;
  audioFileAutoStopBytes = 0;
  if (audioFileCaptureCount == 0) {
    DebugSerial.println("Audio recording stopped with no samples");
    return;
  }

  audioFileUploadSamples = audioFileCaptureCount;
  audioFileReady = true;
  audioFileUploading = true;
  audioFileStartSent = false;
  audioFileSingleAttempted = false;
  audioFileUploadOffset = 0;
  audioFileUploadChunkIndex = 0;
  audioFileUploadTotalChunks = (uint16_t)((AUDIO_FILE_WAV_HEADER_LEN + audioFileUploadSamples + AUDIO_FILE_CHUNK_RAW_BYTES - 1) / AUDIO_FILE_CHUNK_RAW_BYTES);
  audioFileId++;

  DebugSerial.print("Audio recording stopped id=");
  DebugSerial.print(audioFileId);
  DebugSerial.print(" bytes=");
  DebugSerial.print(audioFileUploadSamples);
  DebugSerial.print(" wavBytes=");
  DebugSerial.println(AUDIO_FILE_WAV_HEADER_LEN + audioFileUploadSamples);
}

static uint8_t wavHeaderByte(uint32_t offset) {
  const uint32_t dataBytes = audioFileUploadSamples;
  const uint32_t riffSize = 36 + dataBytes;
  const uint16_t audioFormat = 1;
  const uint16_t channels = 1;
  const uint32_t sampleRate = AUDIO_FILE_SAMPLE_RATE;
  const uint16_t bitsPerSample = AUDIO_FILE_BITS_PER_SAMPLE;
  const uint16_t blockAlign = channels * bitsPerSample / 8;
  const uint32_t byteRate = sampleRate * blockAlign;

  switch (offset) {
    case 0: return 'R';
    case 1: return 'I';
    case 2: return 'F';
    case 3: return 'F';
    case 4: return (uint8_t)(riffSize & 0xFF);
    case 5: return (uint8_t)((riffSize >> 8) & 0xFF);
    case 6: return (uint8_t)((riffSize >> 16) & 0xFF);
    case 7: return (uint8_t)((riffSize >> 24) & 0xFF);
    case 8: return 'W';
    case 9: return 'A';
    case 10: return 'V';
    case 11: return 'E';
    case 12: return 'f';
    case 13: return 'm';
    case 14: return 't';
    case 15: return ' ';
    case 16: return 16;
    case 17: return 0;
    case 18: return 0;
    case 19: return 0;
    case 20: return (uint8_t)(audioFormat & 0xFF);
    case 21: return (uint8_t)((audioFormat >> 8) & 0xFF);
    case 22: return (uint8_t)(channels & 0xFF);
    case 23: return (uint8_t)((channels >> 8) & 0xFF);
    case 24: return (uint8_t)(sampleRate & 0xFF);
    case 25: return (uint8_t)((sampleRate >> 8) & 0xFF);
    case 26: return (uint8_t)((sampleRate >> 16) & 0xFF);
    case 27: return (uint8_t)((sampleRate >> 24) & 0xFF);
    case 28: return (uint8_t)(byteRate & 0xFF);
    case 29: return (uint8_t)((byteRate >> 8) & 0xFF);
    case 30: return (uint8_t)((byteRate >> 16) & 0xFF);
    case 31: return (uint8_t)((byteRate >> 24) & 0xFF);
    case 32: return (uint8_t)(blockAlign & 0xFF);
    case 33: return (uint8_t)((blockAlign >> 8) & 0xFF);
    case 34: return (uint8_t)(bitsPerSample & 0xFF);
    case 35: return (uint8_t)((bitsPerSample >> 8) & 0xFF);
    case 36: return 'd';
    case 37: return 'a';
    case 38: return 't';
    case 39: return 'a';
    case 40: return (uint8_t)(dataBytes & 0xFF);
    case 41: return (uint8_t)((dataBytes >> 8) & 0xFF);
    case 42: return (uint8_t)((dataBytes >> 16) & 0xFF);
    case 43: return (uint8_t)((dataBytes >> 24) & 0xFF);
    default: return 0;
  }
}

static uint8_t audioFileByteAt(uint32_t offset) {
  if (offset < AUDIO_FILE_WAV_HEADER_LEN) {
    return wavHeaderByte(offset);
  }
  return audioFileSamples[offset - AUDIO_FILE_WAV_HEADER_LEN];
}

static void appendAudioFileBase64(String &out, uint32_t offset, uint32_t len) {
  static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

  for (uint32_t i = 0; i < len; i += 3) {
    uint32_t value = ((uint32_t)audioFileByteAt(offset + i)) << 16;
    bool hasB1 = (i + 1) < len;
    bool hasB2 = (i + 2) < len;
    if (hasB1) {
      value |= ((uint32_t)audioFileByteAt(offset + i + 1)) << 8;
    }
    if (hasB2) {
      value |= audioFileByteAt(offset + i + 2);
    }

    out += table[(value >> 18) & 0x3F];
    out += table[(value >> 12) & 0x3F];
    out += hasB1 ? table[(value >> 6) & 0x3F] : '=';
    out += hasB2 ? table[value & 0x3F] : '=';
  }
}

static bool mqttPublishAudioWavBinary(uint32_t totalBytes) {
  if (!mqttConnected && !mqttConnect()) {
    return false;
  }

  uint8_t frameHeader[32];
  memset(frameHeader, 0, sizeof(frameHeader));
  memcpy(frameHeader, "PSWAV01", 7);
  putU32LE(frameHeader + 8, audioFileId);
  putU32LE(frameHeader + 12, AUDIO_FILE_SAMPLE_RATE);
  putU32LE(frameHeader + 16, totalBytes);
  putU32LE(frameHeader + 20, ((audioFileUploadSamples / AUDIO_FILE_BYTES_PER_SAMPLE) * 1000UL) / AUDIO_FILE_SAMPLE_RATE);
  putU16LE(frameHeader + 24, 1);
  putU16LE(frameHeader + 26, AUDIO_FILE_BITS_PER_SAMPLE);

  uint8_t wavHeader[AUDIO_FILE_WAV_HEADER_LEN];
  for (uint8_t i = 0; i < AUDIO_FILE_WAV_HEADER_LEN; i++) {
    wavHeader[i] = wavHeaderByte(i);
  }

  String topicCmd = "AT+CMQTTTOPIC=0,";
  topicCmd += strlen(MQTT_AUDIO_WAV_TOPIC);
  if (!modemWaitPromptAndSend(topicCmd, String(MQTT_AUDIO_WAV_TOPIC), 5000, 5000)) {
    mqttConnected = false;
    return false;
  }

  const uint32_t payloadLen = sizeof(frameHeader) + totalBytes;
  String payloadCmd = "AT+CMQTTPAYLOAD=0,";
  payloadCmd += payloadLen;
  modemDrain(20);
  modemSendLine(payloadCmd);
  if (!modemWaitFor(5000, ">")) {
    mqttConnected = false;
    return false;
  }

  DebugSerial.print("MODEM >>> binary audio length=");
  DebugSerial.println(payloadLen);
  ModemSerial.write(frameHeader, sizeof(frameHeader));
  ModemSerial.write(wavHeader, sizeof(wavHeader));
  ModemSerial.write(audioFileSamples, audioFileUploadSamples);
  if (!modemWaitFor(25000, "OK")) {
    mqttConnected = false;
    return false;
  }

  modemDrain(20);
  modemSendLine("AT+CMQTTPUB=0,1,60");
  if (!modemWaitFor(70000, "+CMQTTPUB: 0,0")) {
    mqttConnected = false;
    return false;
  }

  return true;
}

void captureAudioFileStep() {
  if (!AUDIO_FILE_ENABLED || !audioFileSamples || !audioFileRecording || audioFileReady || audioFileUploading) {
    return;
  }

  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, audioFileReadSamples, sizeof(audioFileReadSamples), &bytesRead, 150);
  if (err != ESP_OK || bytesRead < sizeof(int16_t) * 2) {
    DebugSerial.printf("Audio file read failed: err=%d bytes=%u\n", err, (unsigned)bytesRead);
    return;
  }

  uint16_t inputSamples = (uint16_t)(bytesRead / sizeof(int16_t));
  updateAudioStats(audioFileReadSamples, inputSamples);

  for (uint16_t i = 0; i < inputSamples && audioFileCaptureCount + 1 < audioFileCapacitySamples; i++) {
    int16_t sample = makePcm16Sample(audioFileReadSamples[i]);
    audioFileSamples[audioFileCaptureCount++] = (uint8_t)(sample & 0xFF);
    audioFileSamples[audioFileCaptureCount++] = (uint8_t)((sample >> 8) & 0xFF);
  }

  if (audioFileAutoStopBytes > 0 && audioFileCaptureCount >= audioFileAutoStopBytes) {
    DebugSerial.println("Audio recording reached requested duration; stopping");
    stopAudioRecording();
    return;
  }

  if (audioFileCaptureCount >= audioFileCapacitySamples) {
    DebugSerial.println("Audio recording buffer full; stopping");
    stopAudioRecording();
  }
}

static bool audioFileUploadCanAttempt() {
  if (mqttConnected) {
    return true;
  }

  uint32_t now = millis();
  if (lastAudioFileUploadAttemptMs != 0 && now - lastAudioFileUploadAttemptMs < AUDIO_FILE_UPLOAD_RETRY_MS) {
    return false;
  }

  lastAudioFileUploadAttemptMs = now;
  return true;
}

void processAudioFileUploadStep() {
  if (!AUDIO_FILE_ENABLED || !audioFileSamples || !audioFileUploading || audioFileUploadSamples == 0) {
    return;
  }

  if (!audioFileUploadCanAttempt()) {
    return;
  }

  const uint32_t totalBytes = AUDIO_FILE_WAV_HEADER_LEN + audioFileUploadSamples;

  if (AUDIO_FILE_SINGLE_UPLOAD_ENABLED && !audioFileSingleAttempted) {
    audioFileSingleAttempted = true;
    DebugSerial.print("Audio file binary single upload bytes=");
    DebugSerial.println(totalBytes + 32);
    if (mqttPublishAudioWavBinary(totalBytes)) {
      DebugSerial.print("Audio file binary single upload complete id=");
      DebugSerial.println(audioFileId);
      audioFileCaptureCount = 0;
      audioFileUploadSamples = 0;
      audioFileReady = false;
      audioFileUploading = false;
      audioFileStartSent = false;
      audioFileSingleAttempted = false;
      audioFileUploadOffset = 0;
      audioFileUploadChunkIndex = 0;
      lastAudioFileUploadAttemptMs = 0;
      return;
    }
    DebugSerial.println("Audio file binary single upload failed; falling back to chunks");
    lastAudioFileUploadAttemptMs = 0;
  }

  if (!audioFileStartSent) {
    String startPayload;
    startPayload.reserve(220);
    startPayload += "{\"type\":\"start\",\"fileId\":";
    startPayload += String(audioFileId);
    startPayload += ",\"deviceId\":\"";
    startPayload += DEVICE_ID;
    startPayload += "\",\"format\":\"wav\",\"sampleRate\":";
    startPayload += String(AUDIO_FILE_SAMPLE_RATE);
    startPayload += ",\"channels\":1,\"bits\":";
    startPayload += String(AUDIO_FILE_BITS_PER_SAMPLE);
    startPayload += ",\"durationMs\":";
    startPayload += String(((audioFileUploadSamples / AUDIO_FILE_BYTES_PER_SAMPLE) * 1000UL) / AUDIO_FILE_SAMPLE_RATE);
    startPayload += ",\"size\":";
    startPayload += String(totalBytes);
    startPayload += ",\"chunks\":";
    startPayload += String(audioFileUploadTotalChunks);
    startPayload += "}";

    if (mqttPublishText(MQTT_AUDIO_FILE_TOPIC, startPayload)) {
      audioFileStartSent = true;
      lastAudioFileUploadAttemptMs = 0;
      DebugSerial.print("Audio file start sent id=");
      DebugSerial.println(audioFileId);
    } else {
      DebugSerial.println("Audio file start send failed");
    }
    return;
  }

  if (audioFileUploadOffset < totalBytes) {
    uint16_t rawLen = (uint16_t)min((uint32_t)AUDIO_FILE_CHUNK_RAW_BYTES, totalBytes - audioFileUploadOffset);
    for (uint16_t i = 0; i < rawLen; i++) {
      audioFileChunkBuffer[i] = audioFileByteAt(audioFileUploadOffset + i);
    }

    String chunkPayload;
    chunkPayload.reserve((AUDIO_FILE_CHUNK_RAW_BYTES * 4 / 3) + 180);
    chunkPayload += "{\"type\":\"chunk\",\"fileId\":";
    chunkPayload += String(audioFileId);
    chunkPayload += ",\"index\":";
    chunkPayload += String(audioFileUploadChunkIndex);
    chunkPayload += ",\"offset\":";
    chunkPayload += String(audioFileUploadOffset);
    chunkPayload += ",\"chunks\":";
    chunkPayload += String(audioFileUploadTotalChunks);
    chunkPayload += ",\"size\":";
    chunkPayload += String(totalBytes);
    chunkPayload += ",\"sampleRate\":";
    chunkPayload += String(AUDIO_FILE_SAMPLE_RATE);
    chunkPayload += ",\"channels\":1,\"bits\":";
    chunkPayload += String(AUDIO_FILE_BITS_PER_SAMPLE);
    chunkPayload += ",\"data\":\"";
    appendBase64(chunkPayload, audioFileChunkBuffer, rawLen);
    chunkPayload += "\"}";

    if (mqttPublishText(MQTT_AUDIO_FILE_TOPIC, chunkPayload, 1)) {
      audioFileUploadOffset += rawLen;
      audioFileUploadChunkIndex++;
      lastAudioFileUploadAttemptMs = 0;
      if ((audioFileUploadChunkIndex % 10) == 0 || audioFileUploadOffset >= totalBytes) {
        DebugSerial.print("Audio file chunk ");
        DebugSerial.print(audioFileUploadChunkIndex);
        DebugSerial.print('/');
        DebugSerial.print(audioFileUploadTotalChunks);
        DebugSerial.print(" id=");
        DebugSerial.println(audioFileId);
      }
    } else {
      DebugSerial.print("Audio file chunk send failed id=");
      DebugSerial.print(audioFileId);
      DebugSerial.print(" index=");
      DebugSerial.println(audioFileUploadChunkIndex);
    }
    return;
  }

  String endPayload;
  endPayload.reserve(140);
  endPayload += "{\"type\":\"end\",\"fileId\":";
  endPayload += String(audioFileId);
  endPayload += ",\"chunks\":";
  endPayload += String(audioFileUploadTotalChunks);
  endPayload += ",\"size\":";
  endPayload += String(totalBytes);
  endPayload += ",\"sampleRate\":";
  endPayload += String(AUDIO_FILE_SAMPLE_RATE);
  endPayload += ",\"channels\":1,\"bits\":";
  endPayload += String(AUDIO_FILE_BITS_PER_SAMPLE);
  endPayload += "}";

  if (mqttPublishText(MQTT_AUDIO_FILE_TOPIC, endPayload)) {
    DebugSerial.print("Audio file upload complete id=");
    DebugSerial.println(audioFileId);
    audioFileCaptureCount = 0;
    audioFileUploadSamples = 0;
    audioFileReady = false;
    audioFileUploading = false;
    audioFileStartSent = false;
    audioFileSingleAttempted = false;
    audioFileUploadOffset = 0;
    audioFileUploadChunkIndex = 0;
    lastAudioFileUploadAttemptMs = 0;
  } else {
    DebugSerial.println("Audio file end send failed");
  }
}

bool computeAudioFeatures(const int16_t *samples, int n, float &rmsOut, float &zcrOut, float melOut[AUDIO_MEL_BANDS]) {
  if (!audioFeatureReady || !samples || n <= 1) {
    return false;
  }

  double sumSq = 0.0;
  int zeroCrossings = 0;

  for (int i = 0; i < n; i++) {
    float x = samples[i] / 32768.0f;
    sumSq += x * x;

    if (i > 0) {
      bool prevPositive = samples[i - 1] >= 0;
      bool currPositive = samples[i] >= 0;
      if (prevPositive != currPositive) {
        zeroCrossings++;
      }
    }
  }

  rmsOut = sqrtf(sumSq / n);
  zcrOut = (float)zeroCrossings / (float)(n - 1);

  const float twoPi = 6.28318530718f;

  for (int k = 0; k < AUDIO_FFT_BINS; k++) {
    float step = -twoPi * k / n;
    float rotR = cosf(step);
    float rotI = sinf(step);
    float basisR = 1.0f;
    float basisI = 0.0f;
    float real = 0.0f;
    float imag = 0.0f;

    for (int i = 0; i < n; i++) {
      float x = (samples[i] / 32768.0f) * audioWindow[i];
      real += x * basisR;
      imag += x * basisI;

      float nextR = basisR * rotR - basisI * rotI;
      float nextI = basisR * rotI + basisI * rotR;
      basisR = nextR;
      basisI = nextI;
    }

    audioPower[k] = (real * real + imag * imag) / n;
  }

  for (int m = 0; m < AUDIO_MEL_BANDS; m++) {
    float energy = 0.0f;
    for (int k = 0; k < AUDIO_FFT_BINS; k++) {
      energy += audioPower[k] * audioMelFilters[m][k];
    }
    melOut[m] = logf(energy + AUDIO_EPSILON);
  }

  return true;
}

// =====================================================
// IMU
// =====================================================
bool IMU_I2C_ReadVersion() {
  uint8_t d[3] = {0};
  if (!imuReadBytes(IMU_FUNC_VERSION, d, 3)) {
    return false;
  }
  DebugSerial.printf("IMU Version: %u.%u.%u\n", d[0], d[1], d[2]);
  return true;
}

bool IMU_I2C_ReadAccelerometer(float out[3]) {
  uint8_t d[6];
  if (!imuReadBytes(IMU_FUNC_RAW_ACCEL, d, 6)) return false;
  const float ratio = 16.0f / 32767.0f;
  out[0] = toInt16LE(&d[0]) * ratio;
  out[1] = toInt16LE(&d[2]) * ratio;
  out[2] = toInt16LE(&d[4]) * ratio;
  return true;
}

bool IMU_I2C_ReadGyroscope(float out[3]) {
  uint8_t d[6];
  if (!imuReadBytes(IMU_FUNC_RAW_GYRO, d, 6)) return false;
  const float ratio = (2000.0f / 32767.0f) * (3.1415926f / 180.0f);
  out[0] = toInt16LE(&d[0]) * ratio;
  out[1] = toInt16LE(&d[2]) * ratio;
  out[2] = toInt16LE(&d[4]) * ratio;
  return true;
}

bool IMU_I2C_ReadMagnetometer(float out[3]) {
  uint8_t d[6];
  if (!imuReadBytes(IMU_FUNC_RAW_MAG, d, 6)) return false;
  const float ratio = 800.0f / 32767.0f;
  out[0] = toInt16LE(&d[0]) * ratio;
  out[1] = toInt16LE(&d[2]) * ratio;
  out[2] = toInt16LE(&d[4]) * ratio;
  return true;
}

bool IMU_I2C_ReadQuaternion(float out[4]) {
  uint8_t d[16];
  if (!imuReadBytes(IMU_FUNC_QUAT, d, 16)) return false;
  out[0] = toFloatLE(&d[0]);
  out[1] = toFloatLE(&d[4]);
  out[2] = toFloatLE(&d[8]);
  out[3] = toFloatLE(&d[12]);
  return true;
}

bool IMU_I2C_ReadEuler(float out[3]) {
  uint8_t d[12];
  if (!imuReadBytes(IMU_FUNC_EULER, d, 12)) return false;

  const float RAD2DEG = 57.2957795f;
  out[0] = toFloatLE(&d[0]) * RAD2DEG;
  out[1] = toFloatLE(&d[4]) * RAD2DEG;
  out[2] = toFloatLE(&d[8]) * RAD2DEG;
  return true;
}

bool IMU_I2C_ReadBarometer(float out[4]) {
  uint8_t d[16];
  if (!imuReadBytes(IMU_FUNC_BARO, d, 16)) return false;
  out[0] = toFloatLE(&d[0]);
  out[1] = toFloatLE(&d[4]);
  out[2] = toFloatLE(&d[8]);
  out[3] = toFloatLE(&d[12]);
  return true;
}

bool IMU_I2C_ReadAllFlexible(imu_measurement_t *out) {
  if (!out) return false;

  if (!IMU_I2C_ReadAccelerometer(out->accel)) return false;
  if (!IMU_I2C_ReadGyroscope(out->gyro)) return false;
  if (!IMU_I2C_ReadQuaternion(out->quat)) return false;
  if (!IMU_I2C_ReadEuler(out->euler)) return false;

  if (imuHasMag) {
    if (!IMU_I2C_ReadMagnetometer(out->mag)) {
      imuHasMag = false;
      out->mag[0] = out->mag[1] = out->mag[2] = 0;
      DebugSerial.println("IMU magnetometer not available, disabled");
    }
  } else {
    out->mag[0] = out->mag[1] = out->mag[2] = 0;
  }

  if (imuHasBaro) {
    if (!IMU_I2C_ReadBarometer(out->baro)) {
      imuHasBaro = false;
      out->baro[0] = out->baro[1] = out->baro[2] = out->baro[3] = 0;
      DebugSerial.println("IMU barometer not available, disabled");
    }
  } else {
    out->baro[0] = out->baro[1] = out->baro[2] = out->baro[3] = 0;
  }

  return true;
}

bool initIMU() {
  Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN, IMU_I2C_FREQ);
  delay(100);

  DebugSerial.println("Scanning I2C...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      DebugSerial.printf("I2C found: 0x%02X\n", addr);
    }
  }

  if (!IMU_I2C_ReadVersion()) {
    DebugSerial.println("IMU version read failed");
    return false;
  }

  imuHasMag = true;
  imuHasBaro = true;

  if (!IMU_I2C_ReadAllFlexible(&imuData)) {
    DebugSerial.println("IMU read test failed");
    return false;
  }

  DebugSerial.println("IMU init OK");
  return true;
}

// =====================================================
// 健康模块
// =====================================================
void printHealthPacket(const HealthPacket &p) {
  DebugSerial.print("HR=");
  DebugSerial.print(p.heartRate);
  DebugSerial.print("  SpO2=");
  DebugSerial.print(p.spo2);
  DebugSerial.print("  Micro=");
  DebugSerial.print(p.microCir);
  DebugSerial.print("  SYS=");
  DebugSerial.print(p.systolic);
  DebugSerial.print("  DIA=");
  DebugSerial.print(p.diastolic);
  DebugSerial.print("  RESP=");
  DebugSerial.print(p.respiration);
  DebugSerial.print("  FAT=");
  DebugSerial.print(p.fatigue);
  DebugSerial.print("  RR=");
  DebugSerial.print(p.rrInterval);
  DebugSerial.print("  SDNN=");
  DebugSerial.print(p.hrvSdnn);
  DebugSerial.print("  RMSSD=");
  DebugSerial.print(p.hrvRmssd);
  DebugSerial.print("  BodyT=");
  DebugSerial.print(p.bodyTemp, 1);
  DebugSerial.print("  EnvT=");
  DebugSerial.println(p.envTemp, 1);
}

bool parseHealthFrame(const uint8_t *buf, int len, HealthPacket &out) {
  if (len != HEALTH_FRAME_LEN) return false;
  if (buf[0] != HEALTH_FRAME_HEAD) return false;
  if (buf[1] != HEALTH_FRAME_FIXED) return false;
  if (buf[23] != HEALTH_FRAME_TAIL) return false;

  out.heartRate   = buf[2];
  out.spo2        = buf[3];
  out.microCir    = buf[4];
  out.systolic    = buf[5];
  out.diastolic   = buf[6];
  out.respiration = buf[7];
  out.fatigue     = buf[8];
  out.rrInterval  = buf[9];
  out.hrvSdnn     = buf[10];
  out.hrvRmssd    = buf[11];

  out.bodyTemp = buf[12] + buf[13] / 10.0f;
  out.envTemp  = buf[14] + buf[15] / 10.0f;

  out.valid = true;
  return true;
}

void initHealthModule() {
  HealthSerial.begin(HEALTH_BAUD, SERIAL_8N1, HEALTH_RX_PIN, HEALTH_TX_PIN);
  delay(100);

  while (HealthSerial.available()) {
    HealthSerial.read();
  }

  HealthSerial.write((uint8_t)HEALTH_CMD_START);
  HealthSerial.flush();

  healthStarted = true;
  healthRxIndex = 0;
  healthFrameReceiving = false;
  healthData.valid = false;

  DebugSerial.println("Health module start command sent: 0x24");
}

void stopHealthModule() {
  HealthSerial.write((uint8_t)HEALTH_CMD_STOP);
  HealthSerial.flush();
  healthStarted = false;
  DebugSerial.println("Health module stop command sent: 0x2A");
}

void serviceHealthModule() {
  while (HealthSerial.available()) {
    uint8_t b = HealthSerial.read();

    if (!healthFrameReceiving) {
      if (b == HEALTH_FRAME_HEAD) {
        healthFrameReceiving = true;
        healthRxIndex = 0;
        healthRxBuf[healthRxIndex++] = b;
      }
      continue;
    }

    if (healthRxIndex < HEALTH_FRAME_LEN) {
      healthRxBuf[healthRxIndex++] = b;
    }

    if (healthRxIndex >= HEALTH_FRAME_LEN) {
      HealthPacket pkt;
      if (parseHealthFrame(healthRxBuf, HEALTH_FRAME_LEN, pkt)) {
        healthData = pkt;
      } else {
        DebugSerial.println("Health frame parse failed");
      }

      healthFrameReceiving = false;
      healthRxIndex = 0;
    }
  }
}

// =====================================================
// 串口命令
// UART0 输入命令
// =====================================================
void handleDebugCommand() {
  while (DebugSerial.available()) {
    char c = DebugSerial.read();

    if (c == 'm' || c == 'M') {
      plotMode = MODE_MIC;
      DebugSerial.println("# mode=MIC");
    } else if (c == 'i' || c == 'I') {
      plotMode = MODE_IMU;
      DebugSerial.println("# mode=IMU");
    } else if (c == 'h' || c == 'H') {
      plotMode = MODE_HEALTH;
      DebugSerial.println("# mode=HEALTH");
    } else if (c == 'a' || c == 'A') {
      plotMode = MODE_ALL;
      DebugSerial.println("# mode=ALL");
    } else if (c == 's' || c == 'S') {
      stopHealthModule();
    } else if (c == 'r' || c == 'R') {
      initHealthModule();
    } else if (c == 'b' || c == 'B') {
      startAudioRecording();
    } else if (c == 'u' || c == 'U') {
      stopAudioRecording();
    } else if (c == '1') {
      healthPlotField = HEALTH_PLOT_HR;
      DebugSerial.println("Health plot field = HeartRate");
    } else if (c == '2') {
      healthPlotField = HEALTH_PLOT_SPO2;
      DebugSerial.println("Health plot field = SpO2");
    } else if (c == '3') {
      healthPlotField = HEALTH_PLOT_BODY_TEMP;
      DebugSerial.println("Health plot field = BodyTemp");
    } else if (c == '4') {
      healthPlotField = HEALTH_PLOT_RESP;
      DebugSerial.println("Health plot field = Respiration");
    }
  }
}

// =====================================================
// 输出到 Serial Plotter
// =====================================================
void printMicPlot() {
  size_t sampleCount = 0;
  float rms = 0.0f;
  float zcr = 0.0f;

  if (!readMicFrame(sampleCount) || !computeAudioFeatures(micSamples, (int)sampleCount, rms, zcr, audioMel)) {
    return;
  }

  DebugSerial.print("AUDIOFEAT,");
  DebugSerial.print(audioFrameIndex++);
  DebugSerial.print(',');
  DebugSerial.print(rms, 6);
  DebugSerial.print(',');
  DebugSerial.print(zcr, 6);

  for (int i = 0; i < AUDIO_MEL_BANDS; i++) {
    DebugSerial.print(',');
    DebugSerial.print(audioMel[i], 6);
  }
  DebugSerial.println();
}

void printImuPlot() {
  if (IMU_I2C_ReadAllFlexible(&imuData)) {
    DebugSerial.print("IMU,");
    DebugSerial.print(imuData.accel[0], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.accel[1], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.accel[2], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.gyro[0], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.gyro[1], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.gyro[2], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.mag[0], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.mag[1], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.mag[2], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.quat[0], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.quat[1], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.quat[2], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.quat[3], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.euler[0], 3);
    DebugSerial.print(',');
    DebugSerial.print(imuData.euler[1], 3);
    DebugSerial.print(',');
    DebugSerial.print(imuData.euler[2], 3);
    DebugSerial.print(',');
    DebugSerial.print(imuData.baro[0], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.baro[1], 6);
    DebugSerial.print(',');
    DebugSerial.print(imuData.baro[2], 6);
    DebugSerial.print(',');
    DebugSerial.println(imuData.baro[3], 6);
  }
}

void printHealthPlot() {
  serviceHealthModule();

  DebugSerial.print("HEALTH,");
  DebugSerial.print(healthData.valid ? healthData.heartRate : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.spo2 : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.microCir : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.systolic : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.diastolic : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.respiration : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.fatigue : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.rrInterval : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.hrvSdnn : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.hrvRmssd : 0);
  DebugSerial.print(',');
  DebugSerial.print(healthData.valid ? healthData.bodyTemp : 0.0f, 1);
  DebugSerial.print(',');
  DebugSerial.println(healthData.valid ? healthData.envTemp : 0.0f, 1);
}

void printAllPlot() {
  serviceHealthModule();
  printMicPlot();
  printImuPlot();
  printHealthPlot();
}

// =====================================================
// setup / loop
// =====================================================
void setup() {
  DebugSerial.begin(921600, SERIAL_8N1, 44, 43);
  delay(2000);

  DebugSerial.println("ESP32-S3 PetSense 4G MQTT sensor node start");

  initAudioFeatureExtractor();
  bool micOK = initMicrophone();
  bool audioBufferOK = initAudioFileBuffer();
  imuFound = initIMU();
  initHealthModule();
  bool batteryOK = initBatteryGauge();
  deviceMicOK = micOK;
  deviceAudioBufferOK = audioBufferOK;
  deviceImuOK = imuFound;
  deviceBatteryOK = batteryOK;
  updateBatteryReading(true);

  DebugSerial.printf("Mic: %s\n", micOK ? "OK" : "FAIL");
  DebugSerial.printf("Audio record buffer: %s\n", audioBufferOK ? "OK" : "FAIL");
  DebugSerial.printf("IMU: %s\n", imuFound ? "OK" : "FAIL");
  DebugSerial.printf("Health: STARTED\n");
  DebugSerial.printf("Battery MAX17048: %s on SDA=%d SCL=%d\n",
                     batteryOK ? "OK" : "FAIL",
                     BATTERY_SDA_PIN,
                     BATTERY_SCL_PIN);
  if (batteryData.valid) {
    DebugSerial.printf("Battery initial: %.3f V, %.1f%%\n",
                       batteryData.voltage,
                       batteryData.percent);
  }
  DebugSerial.printf("MQTT broker: %s:%u\n", MQTT_BROKER, MQTT_PORT);
  DebugSerial.printf("MQTT topic: %s\n", MQTT_TOPIC);
  DebugSerial.printf("MQTT audio topic: %s\n", MQTT_AUDIO_TOPIC);
  DebugSerial.printf("MQTT audio file topic: %s\n", MQTT_AUDIO_FILE_TOPIC);
  DebugSerial.printf("MQTT command topic: %s\n", MQTT_COMMAND_TOPIC);
  DebugSerial.printf("Audio stream: %s, %u Hz, 16-bit mono, %u samples/frame\n",
                     AUDIO_STREAM_ENABLED ? "ON" : "OFF",
                     MIC_SAMPLE_RATE,
                     AUDIO_STREAM_SAMPLES);
  DebugSerial.printf("Audio file: %s, WAV %u Hz, %u-bit mono, %u seconds\n",
                     AUDIO_FILE_ENABLED ? "ON" : "OFF",
                     AUDIO_FILE_SAMPLE_RATE,
                     AUDIO_FILE_BITS_PER_SAMPLE,
                     AUDIO_FILE_MAX_SECONDS);
  DebugSerial.printf("Cellular APN: %s\n", CELLULAR_APN);

  bool sdLogOK = LOCAL_SD_CAPTURE_MODE ? initSdLogger(micOK, audioBufferOK, imuFound, batteryOK) : false;
  DebugSerial.printf("Local SD capture mode: %s (%s)\n",
                     LOCAL_SD_CAPTURE_MODE ? "ON" : "OFF",
                     sdLogOK ? "logging" : "not logging");

  if (!LOCAL_SD_CAPTURE_MODE) {
    modemReady = initCellularNetwork();
    if (modemReady) {
      mqttConnect();
    }
  } else {
    DebugSerial.println("Cellular/MQTT disabled while local SD capture mode is ON");
  }

  DebugSerial.printf("Cloud upload interval: %u ms\n", CLOUD_PUBLISH_INTERVAL_MS);
  DebugSerial.println("Commands: s=stop health / r=restart health / b=begin audio / u=upload audio");
}

void loop() {
  handleDebugCommand();
  processModemAsyncInput();
  serviceHealthModule();
  logBatteryReading();

  if (LOCAL_SD_CAPTURE_MODE) {
    sdLogStep();
    return;
  }

  if (sdLoggingActive || (!sdLoggingComplete && sdStatusMessage != "not started")) {
    sdLogStep();
  }

  if (AUDIO_FILE_ENABLED) {
    captureAudioFileStep();
  }

  captureAudioFeatureStep();

  if (AUDIO_STREAM_ENABLED) {
    publishAudioStreamFrame();
  }

  if (AUDIO_FILE_ENABLED && audioFileUploading) {
    for (uint8_t i = 0; i < AUDIO_FILE_UPLOAD_BURST && audioFileUploading; i++) {
      processAudioFileUploadStep();
      processModemAsyncInput();
    }
  }

  uint32_t now = millis();
  if (now - lastCloudSampleMs >= CLOUD_SAMPLE_INTERVAL_MS) {
    lastCloudSampleMs = now;
    appendCloudBatchSample();
  }

  if (cloudBatchCount > 0 &&
      (cloudBatchCount >= CLOUD_BATCH_MAX_FRAMES || now - cloudBatchStartedMs >= CLOUD_PUBLISH_INTERVAL_MS)) {
    publishCloudSnapshot();
    lastCloudPublishMs = millis();
    processModemAsyncInput();
  }

  if (AUDIO_FILE_ENABLED && audioFileUploading) {
    processAudioFileUploadStep();
    processModemAsyncInput();
  }
}
