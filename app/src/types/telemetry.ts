/** 云端遥测数据帧类型，对齐服务端 server.py 广播的 payload 结构 */

export type TelemetryHealth = {
  valid: boolean;
  heartRate: number;
  spo2: number;
  bodyTemp: number;
  envTemp: number;
  respiration: number;
  microCir: number;
  fatigue: number;
  hrvSdnn: number;
  hrvRmssd: number;
  rrInterval: number;
  systolic: number;
  diastolic: number;
};

export type TelemetryBattery = {
  ok: boolean;
  found: boolean;
  percent: number;
  voltage: number;
};

export type TelemetryImu = {
  ok: boolean;
  accel: number[];
  gyro: number[];
  mag: number[];
  euler: number[];
  quat: number[];
  baro: number[];
};

export type TelemetryAudio = {
  ok: boolean;
  sampleRate: number;
  bits: number;
  frame: number;
  rms: number;
  zcr: number;
  mel: number[];
  recording: boolean;
  uploading: boolean;
};

export type TelemetryExperiment = {
  active: boolean;
  id: string;
  eventActive: boolean;
  eventId: string;
  eventLabel: string;
  eventSegment: string;
  sdLogging: boolean;
  sdRows: number;
  sdStatus: string;
  sdAudioMode: string;
};

/** UWB 定位帧：distanceMm 按锚点序号索引（anchorMask 标记哪些槽位有效） */
export type TelemetryUwb = {
  valid: boolean;
  tagId: number;
  anchorId: number;
  sequence: number;
  anchorMask: number;
  kalmanEnabled: boolean;
  distanceMm: number[];
  rawMm: number[];
  kalmanMm: number[];
};

/** 服务端动作识别预测（action_classifier.py 随机森林 1s 窗口推理，随帧广播） */
export type TelemetryAction = {
  source: string;
  modelVersion: string;
  deviceId: string;
  eventId: string;
  label: string;
  actionKey: string;
  confidence: number;
  scores: { eventId: string; label: string; actionKey: string; probability: number }[];
  sampleCount: number;
  windowStartTsMs: number;
  windowEndTsMs: number;
  predictedAt: string;
};

/** 服务端通过 /ws 广播的原始包 */
export type TelemetryPacket = {
  topic: string;
  payload: string | Record<string, unknown>;
  received_at: string;
};

/** 解析后的传感器帧 */
export type TelemetryFrame = {
  deviceId: string;
  /** 设备侧毫秒时间戳 */
  tsMs: number;
  /** 服务端接收时间（UTC ISO 字符串） */
  receivedAt: string;
  health?: TelemetryHealth;
  battery?: TelemetryBattery;
  imu?: TelemetryImu;
  audio?: TelemetryAudio;
  experiment?: TelemetryExperiment;
  uwb?: TelemetryUwb;
  action?: TelemetryAction;
};

export type TelemetryStatus =
  'connecting' | 'open' | 'reconnecting' | 'closed' | 'replaying' | 'unauthenticated';

/**
 * 面向 UI 的展示状态：连接状态 × 数据新鲜度归一后的六态。
 * status 只描述 App↔云端的 WebSocket 连接，设备是否在发数据由 isFresh 补充。
 */
export type TelemetryDisplayState =
  | 'streaming' // 数据流实时更新中
  | 'waiting_device' // 云端已连接，但设备侧无数据
  | 'connecting'
  | 'reconnecting'
  | 'offline'
  | 'replaying'
  | 'unauthenticated';

/** 单指标时间序列点 */
export type SeriesPoint = { t: number; v: number };

export type TelemetrySeries = {
  heartRate: SeriesPoint[];
  spo2: SeriesPoint[];
  bodyTemp: SeriesPoint[];
};

export const SERIES_KEYS = ['heartRate', 'spo2', 'bodyTemp'] as const;
export type SeriesKey = (typeof SERIES_KEYS)[number];
