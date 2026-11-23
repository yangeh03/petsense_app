// W10 Agent 录音协议 psrec01 纯逻辑 host 测试（agent-w10-spec.md §3 / W10-A2）。
// 覆盖：命令解析（合法/协议不匹配/凭据非法/时长越界）、凭据字符集、去重表、
// 时长字节数与回传帧构造（start/chunk/end/status 的关键 JSON 骨架）。
#include "agent_audio_protocol.h"

#include <cstdio>
#include <cstring>
#include <string>

static int failures = 0;

#define CHECK(cond)                                                        \
  do {                                                                     \
    if (!(cond)) {                                                         \
      std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond);           \
      ++failures;                                                          \
    }                                                                      \
  } while (0)

int main() {
  const char *valid =
      "+CMQTTRXSTART noise {\"type\":\"agent_record_audio\",\"proto\":\"psrec01\","
      "\"requestId\":\"Ab3-x_9kQ2Zv7pTnL1wY\",\"durationSec\":7,"
      "\"ts\":\"2026-11-23T10:00:00Z\"} +CMQTTRXEND";

  AgentAudioRequest request;
  CHECK(agentAudioParseCommand(valid, request) == AGENT_AUDIO_OK);
  CHECK(strcmp(request.requestId, "Ab3-x_9kQ2Zv7pTnL1wY") == 0);
  CHECK(request.durationSec == 7);

  // 字段顺序无关
  const char *reordered =
      "{\"durationSec\":1,\"requestId\":\"abcdefgh1234\",\"proto\":\"psrec01\","
      "\"type\":\"agent_record_audio\"}";
  CHECK(agentAudioParseCommand(reordered, request) == AGENT_AUDIO_OK);
  CHECK(request.durationSec == 1 && strcmp(request.requestId, "abcdefgh1234") == 0);

  // 协议不匹配 / 旧命令：静默忽略（ERR_PROTO）
  CHECK(agentAudioParseCommand(
            "{\"type\":\"record_audio_start\",\"proto\":\"psrec01\",\"requestId\":"
            "\"abcdefgh1234\",\"durationSec\":5}",
            request) == AGENT_AUDIO_ERR_PROTO);
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec02\",\"requestId\":"
            "\"abcdefgh1234\",\"durationSec\":5}",
            request) == AGENT_AUDIO_ERR_PROTO);

  // 凭据非法：过短 / 非法字符 / 缺失
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec01\",\"requestId\":\"short\","
            "\"durationSec\":5}",
            request) == AGENT_AUDIO_ERR_REQUEST_ID);
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec01\",\"requestId\":"
            "\"bad id with spaces!!\",\"durationSec\":5}",
            request) == AGENT_AUDIO_ERR_REQUEST_ID);
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec01\",\"durationSec\":5}",
            request) == AGENT_AUDIO_ERR_REQUEST_ID);

  // 时长越界（0 / 11）：requestId 尽力填充供错误回执
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec01\",\"requestId\":"
            "\"abcdefgh1234\",\"durationSec\":0}",
            request) == AGENT_AUDIO_ERR_DURATION);
  CHECK(strcmp(request.requestId, "abcdefgh1234") == 0);
  CHECK(agentAudioParseCommand(
            "{\"type\":\"agent_record_audio\",\"proto\":\"psrec01\",\"requestId\":"
            "\"abcdefgh1234\",\"durationSec\":11}",
            request) == AGENT_AUDIO_ERR_DURATION);

  // 凭据校验边界
  CHECK(agentAudioRequestIdValid("abcdefgh"));      // 8 = 下限
  CHECK(!agentAudioRequestIdValid("abcdefg"));      // 7 = 过短
  std::string long_id(65, 'a');
  CHECK(!agentAudioRequestIdValid(long_id.c_str()));  // 65 = 过长
  std::string max_id(64, 'K');
  CHECK(agentAudioRequestIdValid(max_id.c_str()));
  CHECK(!agentAudioRequestIdValid("abc/def"));      // 非法字符
  CHECK(!agentAudioRequestIdValid(NULL));

  // 去重表：命中 / 未命中 / 空表
  const char *seen[4] = {"id-0001", "id-0002", "", ""};
  CHECK(agentAudioDedupeSeen(seen, 2, "id-0002"));
  CHECK(!agentAudioDedupeSeen(seen, 2, "id-0003"));
  CHECK(!agentAudioDedupeSeen(NULL, 0, "id-0001"));
  CHECK(!agentAudioDedupeSeen(seen, 2, NULL));

  // 时长字节数：16 kHz * 2 字节 * 秒
  CHECK(agentAudioDurationBytes(1) == 32000u);
  CHECK(agentAudioDurationBytes(10) == 320000u);
  CHECK(agentAudioDurationBytes(0) == 32000u);    // 越界钳制
  CHECK(agentAudioDurationBytes(11) == 320000u);

  // status 帧
  char frame[192];
  CHECK(agentAudioStatusFrame(frame, sizeof(frame), "abcdefgh1234", "busy", "recorder_in_use") > 0);
  std::string status(frame);
  CHECK(status.find("\"type\":\"agent_audio_status\"") != std::string::npos);
  CHECK(status.find("\"proto\":\"psrec01\"") != std::string::npos);
  CHECK(status.find("\"requestId\":\"abcdefgh1234\"") != std::string::npos);
  CHECK(status.find("\"status\":\"busy\"") != std::string::npos);
  CHECK(status.find("\"reason\":\"recorder_in_use\"") != std::string::npos);
  CHECK(agentAudioStatusFrame(frame, 16, "abcdefgh1234", "busy", "") == 0);  // 容量不足

  // start 帧：真实 WAV 参数 + 请求/实际时长都携带
  char start[320];
  CHECK(agentAudioStartFrame(start, sizeof(start), "abcdefgh1234", "esp32-a", 160044, 40, 5000, 5000) > 0);
  std::string start_json(start);
  CHECK(start_json.find("\"type\":\"agent_audio_start\"") != std::string::npos);
  CHECK(start_json.find("\"deviceId\":\"esp32-a\"") != std::string::npos);
  CHECK(start_json.find("\"sampleRate\":16000") != std::string::npos);
  CHECK(start_json.find("\"channels\":1") != std::string::npos);
  CHECK(start_json.find("\"bits\":16") != std::string::npos);
  CHECK(start_json.find("\"durationMs\":5000") != std::string::npos);
  CHECK(start_json.find("\"requestedMs\":5000") != std::string::npos);
  CHECK(start_json.find("\"size\":160044") != std::string::npos);
  CHECK(start_json.find("\"chunks\":40") != std::string::npos);

  // chunk 前后缀拼接出合法 JSON 骨架
  char prefix[160], suffix[4];
  CHECK(agentAudioChunkPrefix(prefix, sizeof(prefix), "abcdefgh1234", 3) > 0);
  CHECK(agentAudioChunkSuffix(suffix, sizeof(suffix)) == 2);
  std::string chunk = std::string(prefix) + "QUJD" + suffix;
  CHECK(chunk == "{\"type\":\"agent_audio_chunk\",\"proto\":\"psrec01\","
                 "\"requestId\":\"abcdefgh1234\",\"chunkIndex\":3,\"data\":\"QUJD\"}");

  // end 帧
  char end_frame[160];
  CHECK(agentAudioEndFrame(end_frame, sizeof(end_frame), "abcdefgh1234") > 0);
  std::string end_json(end_frame);
  CHECK(end_json.find("\"type\":\"agent_audio_end\"") != std::string::npos);
  CHECK(end_json.find("\"requestId\":\"abcdefgh1234\"") != std::string::npos);

  if (failures == 0) {
    std::printf("agent audio protocol tests passed\n");
    return 0;
  }
  std::printf("%d check(s) failed\n", failures);
  return 1;
}
