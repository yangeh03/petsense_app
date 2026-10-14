/**
 * 从腾讯云实验数据集（cloud_data/）生成 App 回放数据 src/data/replay-dataset.ts。
 * 一次性工具：先从服务器下载 CSV 到 /tmp，再运行本脚本。
 *
 * 数据源：exp-20260812-074528 + exp-20260812-075945（2026-08-12 同一宠物连续两场实验），
 * 只保留全部指标合理的帧（心率 60-200、体温 35-41.5、血氧 ≥80），按时间顺序拼接，
 * 空档（设备离身）已剔除，回放时按设备原始 2s 节奏重放并循环。
 *
 * 下载命令（在项目根目录执行）：
 *   sshpass -p '<密码>' scp ubuntu@81.71.71.31:/home/ubuntu/petsense-server/cloud_data/exp-20260812-074528/health.csv /tmp/health-a.csv
 *   sshpass -p '<密码>' scp ubuntu@81.71.71.31:/home/ubuntu/petsense-server/cloud_data/exp-20260812-074528/battery.csv /tmp/batt-a.csv
 *   sshpass -p '<密码>' scp ubuntu@81.71.71.31:/home/ubuntu/petsense-server/cloud_data/exp-20260812-075945/health.csv /tmp/health-b.csv
 *   sshpass -p '<密码>' scp ubuntu@81.71.71.31:/home/ubuntu/petsense-server/cloud_data/exp-20260812-075945/battery.csv /tmp/batt-b.csv
 *   node tools/gen-replay-dataset.cjs
 */
const fs = require('fs');

const SOURCES = [
  { experiment: 'exp-20260812-074528', health: '/tmp/health-a.csv', battery: '/tmp/batt-a.csv' },
  { experiment: 'exp-20260812-075945', health: '/tmp/health-b.csv', battery: '/tmp/batt-b.csv' },
];
const BATTERY_EVERY = 30; // 约每分钟保留一个电量样本

// 空值/非法值统一为 0（数据集本身也用 0 表示缺测）
const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n * 100) / 100 : 0;
};
const round2 = num;

function parseCsv(path) {
  const lines = fs.readFileSync(path, 'utf8').trim().split('\n');
  const header = lines[0].split(',');
  return lines.slice(1).map((line) => {
    const cols = line.split(',');
    const row = {};
    header.forEach((h, i) => (row[h] = cols[i]));
    return row;
  });
}

const plausible = (r) =>
  Number(r.heart_rate_bpm) >= 60 &&
  Number(r.heart_rate_bpm) <= 200 &&
  Number(r.body_temp_c) >= 35 &&
  Number(r.body_temp_c) <= 41.5 &&
  Number(r.spo2_pct) >= 80;

// 每场实验取合格帧，全场间按时间顺序拼接
const sessions = SOURCES.map(({ experiment, health, battery }) => {
  const healthRows = parseCsv(health).filter(plausible);
  const tsSet = new Set(healthRows.map((r) => r.device_ts_ms));
  const tsList = [...tsSet];
  const batteryRows = parseCsv(battery).filter((r) => tsSet.has(r.device_ts_ms));
  return { experiment, healthRows, batteryRows, tsList };
});
const ordered = sessions
  .flatMap((s, i) => s.healthRows.map((r) => ({ ...r, src: i })))
  .sort((a, b) => (a.received_at < b.received_at ? -1 : 1));

const hTuples = ordered.map((r) =>
  [
    Number(r.device_ts_ms),
    r.valid === 'True' ? 1 : 0,
    Number(r.heart_rate_bpm),
    Number(r.spo2_pct),
    num(r.body_temp_c),
    num(r.env_temp_c),
    Number(r.respiration_rpm),
    Number(r.micro_circulation),
    Number(r.fatigue),
    Number(r.rr_interval),
    Number(r.hrv_sdnn),
    Number(r.hrv_rmssd),
    Number(r.systolic_mmhg),
    Number(r.diastolic_mmhg),
  ].join(','),
);

const bTuples = sessions
  .flatMap((s) => s.batteryRows.filter((_, i) => i % BATTERY_EVERY === 0))
  .map((r) => [Number(r.device_ts_ms), num(r.percent), num(r.voltage_v)].join(','));

const out = `/**
 * 回放模式数据源：截取自腾讯云真实实验数据集（仅演示用，勿用于科研结论）。
 * 来源: cloud_data/exp-20260812-074528 + exp-20260812-075945（2026-08-12 同一宠物连续两场佩戴实验）。
 * 已剔除设备离身段（心率/血氧/体温不合理的帧），空档压缩，保留时间顺序；
 * 回放按设备原始 2s 节奏循环。重新生成: node tools/gen-replay-dataset.cjs。
 */

/** [tsMs, valid, 心率, 血氧, 体温, 环境温度, 呼吸, 微循环, 疲劳, RR间期, HRV_SDNN, HRV_RMSSD, 收缩压, 舒张压] */
export type ReplayHealthTuple = readonly [
  number, 0 | 1, number, number, number, number, number, number, number, number, number, number, number, number,
];

/** [tsMs, 电量百分比, 电压] */
export type ReplayBatteryTuple = readonly [number, number, number];

export const REPLAY_META = {
  sourceExperiments: ['exp-20260812-074528', 'exp-20260812-075945'] as const,
  deviceId: 'esp32-001',
  recordedAt: '2026-08-12',
  cadenceMs: 2000,
  healthSamples: ${hTuples.length},
  batterySamples: ${bTuples.length},
} as const;

export const REPLAY_HEALTH: readonly ReplayHealthTuple[] = [
${hTuples.map((t) => '  [' + t + '],').join('\n')}
];

export const REPLAY_BATTERY: readonly ReplayBatteryTuple[] = [
${bTuples.map((t) => '  [' + t + '],').join('\n')}
];
`;

fs.writeFileSync(`${__dirname}/../src/data/replay-dataset.ts`, out);
console.log(
  `written: ${hTuples.length} health (${((ordered.length * 2) / 60).toFixed(1)} min), ${bTuples.length} battery rows, ${(out.length / 1024).toFixed(1)}KB`,
);
