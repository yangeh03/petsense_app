import { request } from '@/services/api';
import type { ObservationPeriod, Observations } from '@/types/observations';

export function fetchObservations(period: ObservationPeriod): Promise<Observations> {
  return request(`/observations?period=${period}`);
}
