import type { TelemetryFrame } from '@/types/telemetry';

export type GpsPoint = { latitude: number; longitude: number };
export type GpsView = {
  status: string;
  point: GpsPoint | null;
  utc: string | null;
};

/** Use only the selected collar. A newer no-fix frame supersedes an older fix. */
export function gpsView(
  deviceId: string | null | undefined,
  live: TelemetryFrame | null,
  snapshot: TelemetryFrame | null,
  now: number,
  replaying = false,
): GpsView {
  const empty = (status: string): GpsView => ({ status, point: null, utc: null });
  if (!deviceId) return empty('请先绑定项圈');
  if (replaying) return empty('回放模式不显示实时 GPS');
  const frame = [live, snapshot]
    .filter((value): value is TelemetryFrame => value?.deviceId === deviceId)
    .sort((a, b) => Date.parse(b.receivedAt) - Date.parse(a.receivedAt))[0];
  if (!frame) return empty('等待项圈 GPS 数据');
  const age = now - Date.parse(frame.receivedAt);
  if (!Number.isFinite(age) || age < -5000) return empty('定位时间异常');
  const gps = frame.gps;
  if (!gps) return empty('项圈尚未上报 GPS');
  if (gps.valid && gps.latitude !== null && gps.longitude !== null) {
    if (Math.max(0, age) >= gps.remainingMs) return empty('GPS 定位已过期');
    return {
      status: 'GPS 已定位',
      point: { latitude: gps.latitude, longitude: gps.longitude },
      utc: gps.utc,
    };
  }
  if (age >= 10000) return empty('等待项圈更新 GPS');
  return empty(
    {
      disabled: 'GPS 已关闭',
      starting: 'GPS 启动中',
      searching: 'GPS 搜星中，请将天线置于开阔处',
      stale: 'GPS 定位已过期',
      error: 'GPS 暂不可用',
    }[gps.status] || '等待 GPS 定位',
  );
}
