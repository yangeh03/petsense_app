const assert = require('node:assert/strict');
const { test } = require('node:test');
const { selectDashboardReadings } = require('../src/services/dashboard-readings.ts');
const now = Date.parse('2026-10-25T06:00:00Z');
const pet = { device_id: 'petsense-esp32-001' };
const snapshot = {
  valid: true,
  online: true,
  heartRate: 112,
  spo2: 97,
  bodyTemp: 38.5,
  batteryPercent: 0,
  receivedAt: '2026-10-25T05:59:50Z',
};
function select(overrides = {}) {
  return selectDashboardReadings({
    pet,
    snapshot,
    frame: null,
    displayState: 'offline',
    now,
    ...overrides,
  });
}
test('unbound pet never displays telemetry from another device', () => {
  const result = select({ pet: { device_id: '' } });
  assert.equal(result.state, 'unbound');
  assert.equal(result.vitals, null);
});
test('fresh REST reading is labeled a snapshot and preserves zero battery', () => {
  const result = select();
  assert.equal(result.state, 'snapshot');
  assert.equal(result.vitals.heartRate, 112);
  assert.equal(result.batteryPercent, 0);
});
test('expired REST reading is not presented as current health', () => {
  const result = select({ now: now + 61_000 });
  assert.equal(result.state, 'offline');
  assert.equal(result.vitals, null);
});
test('fresh matching live health wins over REST', () => {
  const result = select({
    displayState: 'streaming',
    frame: {
      deviceId: pet.device_id,
      receivedAt: '2026-10-25T06:00:00Z',
      health: { valid: true, heartRate: 120 },
      battery: { found: true, percent: 0 },
    },
  });
  assert.equal(result.state, 'live');
  assert.equal(result.vitals.heartRate, 120);
  assert.equal(result.batteryPercent, 0);
});
test('invalid live health suppresses older valid REST values', () => {
  const result = select({
    displayState: 'streaming',
    frame: {
      deviceId: pet.device_id,
      health: { valid: false, heartRate: 200 },
    },
  });
  assert.equal(result.state, 'measuring');
  assert.equal(result.vitals, null);
});
test('unrelated live frame cannot change the selected pet readings', () => {
  const result = select({
    displayState: 'streaming',
    frame: {
      deviceId: 'someone-else',
      health: { valid: true, heartRate: 200 },
    },
  });
  assert.equal(result.state, 'snapshot');
  assert.equal(result.vitals.heartRate, 112);
});
test('missing snapshot waits; expired/offline snapshot never supplies health', () => {
  assert.equal(select({ snapshot: null }).state, 'waiting');
  assert.equal(select({ snapshot: { ...snapshot, online: false } }).state, 'offline');
});
test('invalid REST and health-free live IMU do not produce fake vitals', () => {
  const result = select({
    snapshot: { ...snapshot, valid: false },
    displayState: 'streaming',
    frame: { deviceId: pet.device_id, imu: { ok: true } },
  });
  assert.equal(result.state, 'measuring');
  assert.equal(result.vitals, null);
});
