const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

test('location tab uses an explicit indoor/outdoor segmented control without auto switching', () => {
  const source = read('app/(tabs)/tracking.tsx');
  assert.match(source, /useLocationMode/);
  assert.match(source, /accessibilityRole="tablist"/);
  assert.match(source, /accessibilityRole="tab"/);
  assert.match(source, /value: 'indoor',\s*label: '室内'/);
  assert.match(source, /value: 'outdoor',\s*label: '室外'/);
  // 页面不读取遥测来切换模式；两路坐标语义保持独立。
  assert.doesNotMatch(source, /setMode\((?!mode)[a-z]/);
  assert.match(source, /互不混用/);
  assert.match(source, /不会自动切换/);
});

test('indoor panel keeps the real UWB map path and labels the retained last position', () => {
  const source = read('app/(tabs)/tracking.tsx');
  assert.match(source, /<MapViewport/);
  assert.match(source, /usePositionMap/);
  assert.match(source, /router\.push\('\/monitor'\)/);
  assert.match(source, /最后位置/);
  assert.match(source, /EMPTY_TRAIL/);
  // 室内不渲染实时轨迹。
  assert.doesNotMatch(source, /location\.trail/);
  assert.doesNotMatch(source, /appendMeasuredTrail/);
});

test('setup lives on a dedicated stack route and the dog map has its own route', () => {
  const tracking = read('app/(tabs)/tracking.tsx');
  assert.doesNotMatch(tracking, /<UwbSetup/);
  assert.match(tracking, /router\.push\('\/uwb-setup'\)/);
  assert.match(tracking, /router\.push\('\/dog-map'\)/);
  const setup = read('app/uwb-setup.tsx');
  assert.match(setup, /<UwbSetup/);
  assert.match(setup, /onDragging/);
  const dogMap = read('app/dog-map.tsx');
  assert.match(dogMap, /DogMapList/);
  assert.match(dogMap, /DogMapMap/);
  assert.match(dogMap, /DOG_MAP\.capturedAt/);
  const layout = read('app/_layout.tsx');
  assert.match(
    layout,
    /name="uwb-setup"[\s\S]{0,200}headerShown: true[\s\S]{0,80}title: '室内地图设置'/,
  );
  assert.match(layout, /name="dog-map"[\s\S]{0,200}headerShown: true[\s\S]{0,80}title: '遛狗地图'/);
});

test('outdoor panel collapses raw coordinates and never pretends a fix', () => {
  const source = read('app/(tabs)/tracking.tsx');
  assert.match(source, /<GpsMap/);
  assert.match(source, /坐标详情/);
  assert.match(source, /gpsView/);
  assert.match(source, /useObservations/);
  assert.match(source, /先绑定项圈/);
  assert.match(source, /WGS84/);
  assert.doesNotMatch(source, /室外定位筹备中/);
});
