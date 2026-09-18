/**
 * 运行时配置。
 * 遥测地址优先读 EXPO_PUBLIC_TELEMETRY_URL（本地联调时指向本机 mock server），
 * 默认连接当前 PetSense 网关（80 端口），实现设备 → 云端 → APP 的实时链路。
 */
export const TELEMETRY_WS_URL = process.env.EXPO_PUBLIC_TELEMETRY_URL ?? 'ws://81.71.71.31/ws';
