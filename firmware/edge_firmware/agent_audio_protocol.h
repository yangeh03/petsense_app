#pragma once
#include <stddef.h>
#include <stdint.h>

// Agent 远程录音协议 psrec01（W10，agent-w10-spec.md §3）——纯逻辑，host 可测。
// 命令（server → 设备，petsense/device/<id>/command）：
//   {"type":"agent_record_audio","proto":"psrec01","requestId":"<token>",
//    "durationSec":1..10,"ts":"..."}
// 回传（设备 → server，设备作用域 topic petsense/sensor/<id>/agent_audio，全部带同一
// requestId）：agent_audio_start / agent_audio_chunk / agent_audio_end / agent_audio_status。
// 旧 PSWAV01 共享 topic 链路与本协议互不影响；requestId 为高熵凭据，命令与回传一致。

static const uint8_t AGENT_AUDIO_MIN_SECONDS = 1;
static const uint8_t AGENT_AUDIO_MAX_SECONDS = 10;
static const uint16_t AGENT_AUDIO_SAMPLE_RATE = 16000;
static const uint8_t AGENT_AUDIO_BITS_PER_SAMPLE = 16;
static const uint8_t AGENT_AUDIO_CHANNELS = 1;
static const size_t AGENT_AUDIO_REQUEST_ID_MAX = 64;
static const char AGENT_AUDIO_PROTO[] = "psrec01";

enum AgentAudioParse {
  AGENT_AUDIO_OK = 0,
  AGENT_AUDIO_ERR_PROTO = 1,       // type/proto 不匹配：不是本协议的命令，静默忽略
  AGENT_AUDIO_ERR_REQUEST_ID = 2,  // 凭据缺失/非法：无法关联，忽略
  AGENT_AUDIO_ERR_DURATION = 3,    // 时长越界：可回 invalid（requestId 尽力解析）
};

struct AgentAudioRequest {
  char requestId[AGENT_AUDIO_REQUEST_ID_MAX + 1] = {};
  uint8_t durationSec = 0;
};

// 解析命令（字段顺序无关，外层 AT 噪声无害）。返回 AgentAudioParse；
// ERR_DURATION 时 requestId 仍尽力填充供错误回执使用。
int agentAudioParseCommand(const char *json, AgentAudioRequest &out);

// requestId 合法性：8—64 个 URL-safe base64 字符（A-Za-z0-9_-）。
bool agentAudioRequestIdValid(const char *id);

// 最近 capacity 条环形去重表：命中返回 true（重复 MQTT 投递不启动第二次录音）。
bool agentAudioDedupeSeen(const char *const *seen, uint8_t count, const char *requestId);

// 需要的 PCM 字节数（16 kHz 16 bit 单声道）。
uint32_t agentAudioDurationBytes(uint8_t durationSec);

// 回传帧构造：写入调用方缓冲并返回长度；容量不足返回 0。
size_t agentAudioStatusFrame(char *buffer, size_t capacity, const char *requestId,
                             const char *status, const char *reason);
size_t agentAudioStartFrame(char *buffer, size_t capacity, const char *requestId,
                            const char *deviceId, uint32_t wavBytes, uint16_t chunks,
                            uint32_t durationMs, uint32_t requestedMs);
// chunk 帧 = prefix + base64(调用方拼接) + suffix，控制 JSON 骨架归本模块。
size_t agentAudioChunkPrefix(char *buffer, size_t capacity, const char *requestId,
                             uint16_t chunkIndex);
size_t agentAudioChunkSuffix(char *buffer, size_t capacity);
size_t agentAudioEndFrame(char *buffer, size_t capacity, const char *requestId);
