import type { GpsPoint } from '@/services/gps-view';

export type GpsMapSurfaceProps = {
  html: string;
  point: GpsPoint;
  centerRequest: number;
  onMessage: (data: string) => void;
  onError: () => void;
};
