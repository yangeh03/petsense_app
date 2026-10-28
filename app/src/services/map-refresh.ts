import { ApiError } from '@/services/api';
import { checkMapImage } from '@/services/map-image';
import { positionMatchesMap } from '@/services/map-viewport';
import type { UwbMap, UwbPosition } from '@/types/uwb';

type MapVersion = { mapId: string; version: string; revision: string };
type MapRequest = <T>(path: string, options: { signal: AbortSignal }) => Promise<T>;

/** One cache per focused user/device. Version checks never download the PNG unless the binding changes. */
export class MapRefresh {
  private map: UwbMap | null = null;
  private loaded = false;
  private nextCheck = 0;
  private legacy = false;
  private positionKey = '';

  constructor(private request: MapRequest) {}

  clear() {
    this.map = null;
    this.loaded = false;
    this.nextCheck = 0;
    this.positionKey = '';
  }

  async refresh(deviceId: string, position: UwbPosition | null, signal: AbortSignal, now: number) {
    const key = position ? `${position.mapId}:${position.mapVersion}` : '';
    const mismatch =
      key !== this.positionKey && position && !positionMatchesMap(position, this.map);
    if (this.loaded && now < this.nextCheck && !mismatch) return this.map;
    const query = `?deviceId=${encodeURIComponent(deviceId)}`;
    let version: MapVersion | null = null;
    if (!this.legacy) {
      try {
        const response = await this.request<{ deviceId: string; map: MapVersion | null }>(
          `/uwb/map-version${query}`,
          { signal },
        );
        if (!response || response.deviceId !== deviceId || response.map === undefined)
          throw new Error('地图版本数据不完整或设备不匹配');
        version = response.map;
        if (
          version &&
          (typeof version.mapId !== 'string' ||
            typeof version.version !== 'string' ||
            typeof version.revision !== 'string' ||
            !version.revision)
        )
          throw new Error('地图版本数据无效');
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        // Older servers have no metadata endpoint; refresh the full map less frequently.
        this.legacy = true;
      }
    }
    if (!this.legacy && !version) this.map = null;
    else if (
      this.legacy ||
      !this.map ||
      this.map.revision !== version!.revision ||
      this.map.mapId !== version!.mapId ||
      this.map.version !== version!.version
    ) {
      const response = await this.request<{ deviceId: string; map: UwbMap | null }>(
        `/uwb/map${query}`,
        { signal },
      );
      if (!response || response.deviceId !== deviceId || response.map === undefined)
        throw new Error('地图数据不完整或设备不匹配');
      if (response.map) {
        const receipt = checkMapImage(response.map.background);
        this.map = { ...response.map, background: { ...response.map.background, ...receipt } };
      } else this.map = null;
    }
    this.loaded = true;
    this.nextCheck = now + (this.legacy ? 30000 : 5000);
    this.positionKey = key;
    return this.map;
  }
}
