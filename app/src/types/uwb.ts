export type UwbPosition = {
  schemaVersion: 1;
  deviceId: string;
  mapId: string;
  mapVersion: string;
  tagId: number;
  status: 'waiting' | 'ok' | 'low_quality' | 'stale' | 'error';
  fresh: boolean;
  position: { x: number; y: number } | null;
  rawPosition?: { x: number; y: number } | null;
  unconstrainedPosition?: { x: number; y: number } | null;
  boundary?: { constrained: boolean; correctionM: number };
  region?: { id: string; label: string } | null;
  regionStatus?:
    'known' | 'transition' | 'unmapped' | 'unconfigured' | 'uncertain' | 'waiting' | 'stale';
  regionsRevision?: string;
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
  revision?: string;
  name: string;
  units: 'm';
  polygon: [number, number][];
  regions?: {
    id: string;
    label: string;
    polygon: [number, number][];
    priority: number;
    confirmed: boolean;
  }[];
  regionsRevision?: string;
  anchors: { id: number; position: [number, number] }[];
  background: {
    file: string;
    origin: [number, number];
    metersPerPixel: number;
    width: number;
    height: number;
    imageDataUri: string;
    byteLength?: number;
    crc32?: string;
  };
  staleAfterMs: number;
};
