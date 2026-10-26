/** Compatibility hook: reported model action only; no fabricated idle/sleep fallback. */
import { inferPetAction } from '@/services/pet-avatar';
import type { TelemetryFrame } from '@/types/telemetry';

export { inferPetAction } from '@/services/pet-avatar';
export type { PetActionKey } from '@/services/pet-avatar';

export function usePetAction(latest: TelemetryFrame | null | undefined) {
  return inferPetAction(latest);
}
