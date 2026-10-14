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
 * 默认连线上腾讯云服务，实现硬件设备 → 云端 → APP 的跨网实时链路。
 */
export const TELEMETRY_WS_URL = process.env.EXPO_PUBLIC_TELEMETRY_URL ?? 'ws://81.71.71.31:8080/ws';
