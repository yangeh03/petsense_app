/** 遛狗地图（广州南沙路段评分快照）的类型。数据与来源说明见 docs/DOG_MAP.md。 */

/** 生成文件的紧凑行：[lon, lat, 名称, 评分, 源等级, 绿视率%, 人行道%, 交通暴露%, 人流占比%, 标签] */
export type DogMapPointTuple = readonly [
  number,
  number,
  string,
  number,
  string,
  number,
  number,
  number,
  number,
  string,
];

export type DogMapSource = {
  name: string;
  url: string;
  /** 上游坐标系的记录，例如 GCJ-02 → WGS-84 近似逆变换。 */
  crs: string;
};

export type DogMapCoverage = { west: number; east: number; south: number; north: number };

/** 生成文件的原始形态：元数据 + 紧凑点行。 */
export type DogMapSnapshotData = {
  version: string;
  /** 快照抓取日期（ISO 日期）；上游未提供原始采集时间。 */
  capturedAt: string;
  source: DogMapSource;
  coverage: DogMapCoverage;
  points: DogMapPointTuple[];
};

/** 展开后的单条评分，供列表、详情与地图使用。 */
export type DogMapPoint = {
  longitude: number;
  latitude: number;
  name: string;
  /** 源评分（0–100 整数），不重算、不改写。 */
  score: number;
  /** 源等级原文（适合遛狗/一般/较差/不适合遛狗），不重算。 */
  grade: string;
  gviPercent: number;
  sidewalkPercent: number;
  trafficPercent: number;
  pedestrianPercent: number;
  tags: string[];
};

export type DogMapSnapshot = Omit<DogMapSnapshotData, 'points'> & { points: DogMapPoint[] };
