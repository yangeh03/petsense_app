import { DOG_MAP_SNAPSHOT } from '../generated/dog-map-data';

import type { DogMapPoint, DogMapPointTuple, DogMapSnapshot } from '@/types/dog-map';

/** 上游未命名路段在界面上的展示名；数据里保留原始名称。 */
export function dogMapPointName(point: Pick<DogMapPoint, 'name'>): string {
  return /^unnamed_/.test(point.name) ? '未命名道路' : point.name;
}

/** 展开生成文件的紧凑行；标签按源页面的「、」分隔。 */
export function expandDogMapPoint(row: DogMapPointTuple): DogMapPoint {
  return {
    longitude: row[0],
    latitude: row[1],
    name: row[2],
    score: row[3],
    grade: row[4],
    gviPercent: row[5],
    sidewalkPercent: row[6],
    trafficPercent: row[7],
    pedestrianPercent: row[8],
    tags: row[9] ? row[9].split('、').filter(Boolean) : [],
  };
}

/** 当前打包的离线快照；版本与来源随数据走，更新流程见 docs/DOG_MAP.md。 */
export const DOG_MAP: DogMapSnapshot = {
  ...DOG_MAP_SNAPSHOT,
  points: DOG_MAP_SNAPSHOT.points.map(expandDogMapPoint),
};
