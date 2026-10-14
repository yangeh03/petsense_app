/**
 * 运行时配置。
 * 遥测地址优先读 EXPO_PUBLIC_TELEMETRY_URL（本地联调时指向本机 mock server），
 * 默认连线上腾讯云服务，实现硬件设备 → 云端 → APP 的跨网实时链路。
 */
export const TELEMETRY_WS_URL = process.env.EXPO_PUBLIC_TELEMETRY_URL ?? 'ws://81.71.71.31:8080/ws';
