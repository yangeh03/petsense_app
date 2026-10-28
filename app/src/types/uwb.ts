export type UwbPosition = {
  schemaVersion: 1;
  deviceId: string;
  mapId: string;
  mapVersion: string;
  tagId: number;
  status: 'waiting' | 'ok' | 'low_quality' | 'stale' | 'error';
  fresh: boolean;
  position: { x: number; y: number } | null;
  quality: { score: number | null; residualM: number | null; insideMap: boolean | null };
  remainingMs: number;
  expiresAt: string | null;
  sequence: number | null;
};

export type UwbMap = {
  schemaVersion: 1;
  deviceId?: string;
  mapId: string;
  version: string;
  name: string;
  units: 'm';
  polygon: [number, number][];
  anchors: { id: number; position: [number, number] }[];
  background: {
    file: string;
    origin: [number, number];
    metersPerPixel: number;
    width: number;
    height: number;
    imageDataUri: string;
  };
  staleAfterMs: number;
};
