const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

test('records keeps the stack header and drops the duplicated in-body title', () => {
  const source = read('app/records.tsx');
  const layout = read('app/_layout.tsx');
  // Stack 标题和返回保留
  assert.match(layout, /name="records"[\s\S]{0,120}headerShown: true, title: '记录'/);
  // 正文不再渲染重复标题
  assert.doesNotMatch(source, /styles\.title/);
  assert.doesNotMatch(source, /只汇总项圈实际观测到的数据/);
});

test('records offsets the overlaying iOS glass header with the measured header height', () => {
  const source = read('app/records.tsx');
  // iOS 玻璃导航栏悬浮在内容之上（不自动让位），按实测头部高度避让；
  // Android 原生头部已把内容排在下方，不能重复加头部高度。
  assert.match(source, /import \{ useHeaderHeight \} from 'expo-router\/react-navigation'/);
  assert.match(source, /const headerHeight = useHeaderHeight\(\)/);
  assert.match(
    source,
    /paddingTop: \(Platform\.OS === 'ios' \? headerHeight : 0\) \+ Spacing\.four/,
  );
  // 不手动累加状态栏高度（headerHeight 已含状态栏）
  assert.doesNotMatch(source, /paddingTop: insets\.top/);
  // 头部之下仍保留内容间距，不做纯固定值假设
  assert.doesNotMatch(source, /paddingTop: Spacing\.four[,}]/);
});

test('records source card labels total and action-judged coverage separately', () => {
  const source = read('app/records.tsx');
  assert.match(source, /总观测覆盖/);
  assert.match(source, /有动作判定/);
  assert.match(source, /selected\.observedSeconds/);
  assert.match(source, /selected\.actionObservedSeconds/);
});
