#include "agent_audio_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Agent 远程录音协议 psrec01 的纯逻辑实现（W10，agent-w10-spec.md §3）。
// 不依赖 Arduino HAL：命令解析、凭据校验、去重表与回传帧构造都在 host 可测。

static const char COMMAND_TYPE[] = "agent_record_audio";

static bool idChar(char ch) {
  return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
         (ch >= '0' && ch <= '9') || ch == '-' || ch == '_';
}

bool agentAudioRequestIdValid(const char *id) {
  if (!id) {
    return false;
  }
  size_t length = strlen(id);
  if (length < 8 || length > AGENT_AUDIO_REQUEST_ID_MAX) {
    return false;
  }
  for (size_t i = 0; i < length; ++i) {
    if (!idChar(id[i])) {
      return false;
    }
  }
  return true;
}

static const char *findKey(const char *json, const char *key) {
  char needle[32];
  snprintf(needle, sizeof(needle), "\"%s\"", key);
  const char *pos = strstr(json, needle);
  if (!pos) {
    return NULL;
  }
  pos += strlen(needle);
  while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n') {
    ++pos;
  }
  if (*pos != ':') {
    return NULL;
  }
  ++pos;
  while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n') {
    ++pos;
  }
  return pos;
}

static bool readStringValue(const char *json, const char *key, char *out, size_t capacity) {
  const char *pos = findKey(json, key);
  if (!pos || *pos != '"') {
    return false;
  }
  ++pos;
  size_t used = 0;
  while (pos && *pos && *pos != '"') {
    // 本协议字段（proto/requestId）不允许转义与控制字符，出现即按非法处理。
    if (used + 1 >= capacity || (unsigned char)*pos < 0x20 || *pos == '\\') {
      return false;
    }
    out[used++] = *pos++;
  }
  if (*pos != '"') {
    return false;
  }
  out[used] = '\0';
  return true;
}

static bool readIntValue(const char *json, const char *key, long &out) {
  const char *pos = findKey(json, key);
  if (!pos || (*pos < '0' && *pos != '-')) {
    return false;
  }
  char *end = NULL;
  long value = strtol(pos, &end, 10);
  if (end == pos) {
    return false;
  }
  out = value;
  return true;
}

int agentAudioParseCommand(const char *json, AgentAudioRequest &out) {
  if (!json) {
    return AGENT_AUDIO_ERR_PROTO;
  }
  char type[48] = {};
  char proto[24] = {};
  if (!readStringValue(json, "type", type, sizeof(type)) ||
      strcmp(type, COMMAND_TYPE) != 0) {
    return AGENT_AUDIO_ERR_PROTO;
  }
  // proto 缺失或版本不同：不属于本协议版本，静默忽略（服务端按能力门控）。
  if (!readStringValue(json, "proto", proto, sizeof(proto)) ||
      strcmp(proto, AGENT_AUDIO_PROTO) != 0) {
    return AGENT_AUDIO_ERR_PROTO;
  }
  if (!readStringValue(json, "requestId", out.requestId, sizeof(out.requestId)) ||
      !agentAudioRequestIdValid(out.requestId)) {
    out.requestId[0] = '\0';
    return AGENT_AUDIO_ERR_REQUEST_ID;
  }
  long duration = 0;
  if (!readIntValue(json, "durationSec", duration) ||
      duration < AGENT_AUDIO_MIN_SECONDS || duration > AGENT_AUDIO_MAX_SECONDS) {
    return AGENT_AUDIO_ERR_DURATION;
  }
  out.durationSec = (uint8_t)duration;
  return AGENT_AUDIO_OK;
}

bool agentAudioDedupeSeen(const char *const *seen, uint8_t count, const char *requestId) {
  if (!seen || !requestId) {
    return false;
  }
  for (uint8_t i = 0; i < count; ++i) {
    if (seen[i] && strcmp(seen[i], requestId) == 0) {
      return true;
    }
  }
  return false;
}

uint32_t agentAudioDurationBytes(uint8_t durationSec) {
  if (durationSec < AGENT_AUDIO_MIN_SECONDS) {
    durationSec = AGENT_AUDIO_MIN_SECONDS;
  }
  if (durationSec > AGENT_AUDIO_MAX_SECONDS) {
    durationSec = AGENT_AUDIO_MAX_SECONDS;
  }
  return (uint32_t)durationSec * AGENT_AUDIO_SAMPLE_RATE *
         (AGENT_AUDIO_BITS_PER_SAMPLE / 8) * AGENT_AUDIO_CHANNELS;
}

size_t agentAudioStatusFrame(char *buffer, size_t capacity, const char *requestId,
                             const char *status, const char *reason) {
  if (!buffer || !requestId || !status) {
    return 0;
  }
  int written = snprintf(buffer, capacity,
                         "{\"type\":\"agent_audio_status\",\"proto\":\"%s\","
                         "\"requestId\":\"%s\",\"status\":\"%s\",\"reason\":\"%s\"}",
                         AGENT_AUDIO_PROTO, requestId, status, reason ? reason : "");
  return (written > 0 && (size_t)written < capacity) ? (size_t)written : 0;
}

size_t agentAudioStartFrame(char *buffer, size_t capacity, const char *requestId,
                            const char *deviceId, uint32_t wavBytes, uint16_t chunks,
                            uint32_t durationMs, uint32_t requestedMs) {
  if (!buffer || !requestId || !deviceId) {
    return 0;
  }
  int written = snprintf(
      buffer, capacity,
      "{\"type\":\"agent_audio_start\",\"proto\":\"%s\",\"requestId\":\"%s\","
      "\"deviceId\":\"%s\",\"format\":\"wav\",\"sampleRate\":%u,\"channels\":%u,"
      "\"bits\":%u,\"durationMs\":%lu,\"requestedMs\":%lu,\"size\":%lu,\"chunks\":%u}",
      AGENT_AUDIO_PROTO, requestId, deviceId, (unsigned)AGENT_AUDIO_SAMPLE_RATE,
      (unsigned)AGENT_AUDIO_CHANNELS, (unsigned)AGENT_AUDIO_BITS_PER_SAMPLE,
      (unsigned long)durationMs, (unsigned long)requestedMs, (unsigned long)wavBytes,
      (unsigned)chunks);
  return (written > 0 && (size_t)written < capacity) ? (size_t)written : 0;
}

size_t agentAudioChunkPrefix(char *buffer, size_t capacity, const char *requestId,
                             uint16_t chunkIndex) {
  if (!buffer || !requestId) {
    return 0;
  }
  int written = snprintf(buffer, capacity,
                         "{\"type\":\"agent_audio_chunk\",\"proto\":\"%s\","
                         "\"requestId\":\"%s\",\"chunkIndex\":%u,\"data\":\"",
                         AGENT_AUDIO_PROTO, requestId, (unsigned)chunkIndex);
  return (written > 0 && (size_t)written < capacity) ? (size_t)written : 0;
}

size_t agentAudioChunkSuffix(char *buffer, size_t capacity) {
  if (!buffer || capacity < 3) {
    return 0;
  }
  buffer[0] = '"';
  buffer[1] = '}';
  buffer[2] = '\0';
  return 2;
}

size_t agentAudioEndFrame(char *buffer, size_t capacity, const char *requestId) {
  if (!buffer || !requestId) {
    return 0;
  }
  int written = snprintf(buffer, capacity,
                         "{\"type\":\"agent_audio_end\",\"proto\":\"%s\","
                         "\"requestId\":\"%s\"}",
                         AGENT_AUDIO_PROTO, requestId);
  return (written > 0 && (size_t)written < capacity) ? (size_t)written : 0;
}
