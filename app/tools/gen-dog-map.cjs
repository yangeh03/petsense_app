// 遛狗地图数据导入工具：从 CityScene 南沙演示地图的公开 HTML 快照提取路段评分点，
// 将 GCJ-02 坐标转换为 WGS84 近似值，生成 `src/generated/dog-map-data.ts`。
// 来源、坐标假设与更新流程见 docs/DOG_MAP.md。运行：node tools/gen-dog-map.cjs [--source map.html]
//
// 只借用上游 HTML 的静态标记与坐标处理，不引入其瓦片、脚本或运行时访问。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const SOURCE_URL = 'https://qioyo.site/petsenseIMG/dogmap/';
const OUT_FILE = path.join(__dirname, '../src/generated/dog-map-data.ts');
const EXPECTED_COUNT = 663;

// --- GCJ-02 → WGS-84 近似逆变换（误差约 1–2 m；源页面自身声明 coordtype=gcj02ll，瓦片为高德） ---
function gcj02ToWgs84(lat, lon) {
  const x = lon - 105;
  const y = lat - 35;
  let dLat = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  dLat += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  dLat += ((20 * Math.sin(y * Math.PI) + 40 * Math.sin((y / 3) * Math.PI)) * 2) / 3;
  dLat += ((160 * Math.sin((y / 12) * Math.PI) + 320 * Math.sin((y / 30) * Math.PI)) * 2) / 3;
  let dLon = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  dLon += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  dLon += ((20 * Math.sin(x * Math.PI) + 40 * Math.sin((x / 3) * Math.PI)) * 2) / 3;
  dLon += ((150 * Math.sin((x / 12) * Math.PI) + 300 * Math.sin((x / 30) * Math.PI)) * 2) / 3;
  const a = 6378245;
  const ee = 0.006693421622965943;
  const rad = (lat / 180) * Math.PI;
  const magic = 1 - ee * Math.sin(rad) ** 2;
  const sqrtMagic = Math.sqrt(magic);
  const latDelta = (dLat * 180) / (((a * (1 - ee)) / (magic * sqrtMagic)) * Math.PI);
  const lonDelta = (dLon * 180) / ((a / sqrtMagic) * Math.cos(rad) * Math.PI);
  return [lat - latDelta, lon - lonDelta];
}

// 上游以 Leaflet circleMarker 直接标注在高德瓦片上，tooltip 内含名称/评分/等级/指标/标签。
const MARKER_PATTERN =
  /L\.circleMarker\(\s*\[([\d.]+), ([\d.]+)\](?:(?!L\.circleMarker\()[\s\S])*?bindTooltip\(\s*`<div>\s*([^<]+?)：([0-9]+) 分（([^）]+)）<br>绿视率 ([\d.]+) \| 人行道 ([\d.]+)<br>交通暴露 ([\d.]+) \| 人流 ([\d.]+)(?:<br>标签：([^<]*))?/;

function parseArgs(argv) {
  const args = { source: null };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--source') args.source = argv[++i];
    else throw new Error(`未知参数 ${argv[i]}`);
  }
  return args;
}

async function main() {
  const { source } = parseArgs(process.argv);
  const htmlPath = source ?? path.join(fs.mkdtempSync('/tmp/petsense-dogmap-'), 'map.html');
  if (!source) {
    console.log(`下载源快照 ${SOURCE_URL} → ${htmlPath}`);
    execFileSync(
      'curl',
      ['-fsSL', '--retry', '2', '--max-time', '30', SOURCE_URL, '-o', htmlPath],
      {
        stdio: 'inherit',
      },
    );
  }
  const html = fs.readFileSync(htmlPath, 'utf8');
  // 源 CRS 证据：高德瓦片 + coordtype=gcj02ll。任一缺失即拒绝生成，避免坐标假设失效。
  assert.match(html, /is\.autonavi\.com/, '源页面未使用高德（GCJ-02）瓦片，坐标假设不成立');
  assert.match(html, /coordtype[\s"',=:]+gcj02ll/i, '源页面未声明 coordtype=gcj02ll');

  const points = [];
  for (const row of html.matchAll(new RegExp(MARKER_PATTERN.source, 'g'))) {
    // matchAll 的首个元素是完整匹配，捕获组从 1 开始（与 Python re.findall 不同）。
    const [lat, lon, name, score, grade, gvi, walk, traffic, people, tags] = row.slice(1);
    const [wgsLat, wgsLon] = gcj02ToWgs84(parseFloat(lat), parseFloat(lon));
    points.push([
      Number(wgsLon.toFixed(6)),
      Number(wgsLat.toFixed(6)),
      name.trim(),
      Number.parseInt(score, 10),
      grade.trim(),
      ...[gvi, walk, traffic, people].map((n) => Number((parseFloat(n) * 100).toFixed(1))),
      tags ? tags.trim() : '',
    ]);
  }
  assert.equal(
    points.length,
    EXPECTED_COUNT,
    `提取到 ${points.length} 个评分点，预期 ${EXPECTED_COUNT}；上游格式可能已变化，先核对源页面`,
  );
  for (const point of points) {
    const [lon, lat, name, score, grade] = point;
    assert.ok(Number.isFinite(lon) && Number.isFinite(lat), '存在无效坐标');
    assert.ok(lon > 112 && lon < 115 && lat > 22 && lat < 24, `坐标超出南沙范围：${name}`);
    assert.ok(Number.isInteger(score) && score >= 0 && score <= 100, `评分超界：${name}`);
    assert.ok(['适合遛狗', '一般', '较差', '不适合遛狗'].includes(grade), `未知等级：${grade}`);
    assert.ok(
      point.slice(5, 9).every((n) => n >= 0 && n <= 100),
      `指标超界：${name}`,
    );
  }
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const capturedAt = new Date().toISOString().slice(0, 10);
  const snapshot = {
    version: `nansha-${capturedAt}`,
    capturedAt,
    source: {
      name: 'CityScene 南沙演示地图',
      url: SOURCE_URL,
      crs: 'GCJ-02（高德瓦片；coordtype=gcj02ll）→ WGS-84 近似逆变换',
    },
    coverage: {
      west: Math.min(...lons),
      east: Math.max(...lons),
      south: Math.min(...lats),
      north: Math.max(...lats),
    },
    points,
  };
  const literal = JSON.stringify(snapshot, null, 1).replace(/</g, '\\u003c').replace(/\n/g, '\n');
  const banner = `/**
 * 遛狗地图离线快照（广州南沙路段评分）。由 tools/gen-dog-map.cjs 生成，请勿手改；
 * 来源、坐标假设、覆盖范围与更新流程见 docs/DOG_MAP.md。
 */
import type { DogMapSnapshotData } from '../types/dog-map';

export const DOG_MAP_SNAPSHOT: DogMapSnapshotData = ${literal};
`;
  fs.writeFileSync(OUT_FILE, banner);
  console.log(
    `写入 ${OUT_FILE}：${points.length} 个点，版本 ${snapshot.version}，` +
      `覆盖 lon ${snapshot.coverage.west}–${snapshot.coverage.east}，lat ${snapshot.coverage.south}–${snapshot.coverage.north}`,
  );
}

main().catch((reason) => {
  console.error(reason instanceof Error ? reason.message : reason);
  process.exitCode = 1;
});
