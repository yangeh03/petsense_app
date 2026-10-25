import type { DashboardTelemetry, SocialPet } from '@/types/social';
import type { TelemetryDisplayState, TelemetryFrame } from '@/types/telemetry';

export type ReadingState = 'unbound' | 'waiting' | 'offline' | 'measuring' | 'snapshot' | 'live';

/** 只展示当前宠物设备的新鲜、有效体征；最近记录与实时流明确区分。 */
export function selectDashboardReadings({
  pet,
  snapshot,
  frame,
  displayState,
  now = Date.now(),
}: {
  pet: Pick<SocialPet, 'device_id'> | null;
  snapshot: DashboardTelemetry | null | undefined;
  frame: TelemetryFrame | null;
  displayState: TelemetryDisplayState;
  now?: number;
}) {
  const live =
    pet?.device_id && frame?.deviceId === pet.device_id && displayState === 'streaming'
      ? frame
      : null;
  const age = snapshot ? now - Date.parse(snapshot.receivedAt) : Infinity;
  const recent = !!snapshot?.online && Number.isFinite(age) && age >= -5000 && age < 60_000;
  let state: ReadingState;
  if (!pet?.device_id) state = 'unbound';
  else if (live?.health) state = live.health.valid ? 'live' : 'measuring';
  else if (recent) state = snapshot?.valid ? 'snapshot' : 'measuring';
  else if (live) state = 'measuring';
  else state = snapshot ? 'offline' : 'waiting';

  return {
    state,
    online: state === 'live' || state === 'snapshot' || state === 'measuring',
    vitals: state === 'live' ? live!.health! : state === 'snapshot' ? snapshot! : null,
    batteryPercent:
      state === 'unbound'
        ? null
        : live?.battery?.found
          ? Math.round(live.battery.percent)
          : (snapshot?.batteryPercent ?? null),
    updatedAt: live?.receivedAt ?? snapshot?.receivedAt ?? null,
  };
}
