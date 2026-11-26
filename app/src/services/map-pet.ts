import { selectPetAvatar } from '@/services/pet-avatar';
import type { DashboardData } from '@/types/social';
import type { TelemetryFrame } from '@/types/telemetry';

export type MapPet = { animationUrl: string | null; label: string; current: boolean; name: string };

/** Share the dashboard's device, confidence and identity checks; never preview an action here. */
export function selectMapPet(
  dashboard: DashboardData | null,
  frame: TelemetryFrame | null,
  fresh: boolean,
): MapPet {
  const pet = dashboard?.pet;
  if (!pet) return { animationUrl: null, label: '动作待识别', current: false, name: '宠物' };
  const media = selectPetAvatar({
    pet,
    petgif: dashboard.petgif,
    avatarTemplate: dashboard.avatarTemplate,
    frame,
    fresh,
  });
  const current = fresh && media.actionKey !== null;
  return {
    animationUrl: current ? media.animationUrl : null,
    label: current ? media.label! : '动作待识别',
    current,
    name: pet.name,
  };
}
