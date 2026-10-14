/**
 * 运行时配置。
 * 遥测模式：'live' 连云端 WebSocket 收硬件实时数据（默认）；
 *           'replay' 本地循环回放 src/data/replay-dataset.ts 里的实验数据集片段（离线可用）。
 * 用 EXPO_PUBLIC_TELEMETRY_MODE 覆盖（打包期注入，改后需重启 Metro）。
 */
export type TelemetryMode = 'live' | 'replay';

export const TELEMETRY_MODE: TelemetryMode =
  process.env.EXPO_PUBLIC_TELEMETRY_MODE === 'replay' ? 'replay' : 'live';

/**
 * 实时模式遥测地址。优先读 EXPO_PUBLIC_TELEMETRY_URL（本地联调时指向本机 mock server），
 * 默认连接当前 PetSense 网关（80 端口），实现设备 → 云端 → APP 的实时链路。
 */
export const TELEMETRY_WS_URL = process.env.EXPO_PUBLIC_TELEMETRY_URL ?? 'ws://81.71.71.31/ws';

/**
 * App 后端 REST 地址（账号 / 社区 / 每日统计）。
 * 真机调试时把 localhost 换成电脑局域网 IP，或用 EXPO_PUBLIC_API_BASE_URL 覆盖。
 */
export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://localhost:8080';
