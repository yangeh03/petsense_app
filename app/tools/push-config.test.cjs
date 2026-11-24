/**
 * W13 原生配置守卫：本轮只交付 iOS，Android 原生配置不在本包范围内。
 *
 * 断言内容随负责人决策变化，配置被误改时先在这里失败。
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const appConfig = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
const easConfig = JSON.parse(fs.readFileSync(path.join(root, 'eas.json'), 'utf8'));

function notificationPlugin() {
  const plugins = appConfig.expo.plugins ?? [];
  const entry = plugins.find(
    (item) => (Array.isArray(item) ? item[0] : item) === 'expo-notifications',
  );
  if (!entry) return null;
  return Array.isArray(entry) ? entry[1] : {};
}

test('iOS 应用标识与 EAS project ID 已配置', () => {
  assert.equal(appConfig.expo.ios.bundleIdentifier, 'com.yangeh03.petsense');
  assert.ok(appConfig.expo.extra?.eas?.projectId, '使用项目 EAS project ID');
  assert.equal(appConfig.expo.runtimeVersion?.policy, 'fingerprint');
});

test('expo-notifications 插件开启远程通知', () => {
  const plugin = notificationPlugin();
  assert.ok(plugin, 'app.json 接入 expo-notifications 配置插件');
  assert.equal(plugin.enableRemoteNotifications, true, 'iOS 需要 APNs entitlement');
  assert.equal(plugin.enableBackgroundRemoteNotifications, false, '本包不用静默推送');
});

test('Android 原生配置不在本包范围：不设 application ID，也不配通知图标', () => {
  assert.equal(
    appConfig.expo.android?.package,
    undefined,
    '本轮只交付 iOS，不改 Android application ID 与原生配置',
  );
  const plugin = notificationPlugin() ?? {};
  assert.equal(plugin.color, undefined, '颜色是 Android 专属配置，本轮不设');
  assert.equal(plugin.defaultChannel, undefined, '通知渠道是 Android 专属配置，本轮不设');
});

test('EAS 内部分发构建档位存在且只发 iOS 真机', () => {
  const internal = easConfig.build?.internal;
  assert.ok(internal, '提供 internal 构建档位用于 EAS 内部分发');
  assert.equal(internal.distribution, 'internal', '内部分发不产出公开产物');
  assert.equal(internal.ios.simulator, false, '只发真机');
  assert.equal(internal.ios.device, undefined, 'eas config 不接受 ios.device 字段');
  assert.equal(internal.android, undefined, '本轮不产出 Android 构建');
});

test('EAS 档位不使用 schema 不接受的字段', () => {
  for (const [name, profile] of Object.entries(easConfig.build ?? {})) {
    assert.equal(profile.ios?.device, undefined, `${name} 不应带 ios.device`);
    assert.equal(profile.android?.device, undefined, `${name} 不应带 android.device`);
  }
});

test('生产档位仍走 production 通道，OTA 不受影响', () => {
  assert.equal(easConfig.build?.production?.channel, 'production');
  assert.equal(easConfig.build?.internal?.channel, 'production', '内部分发构建接收同一 OTA 通道');
});

test('真机包的 API 地址与 production OTA 一致，不会连到设备自身 localhost', () => {
  for (const name of ['internal', 'production']) {
    assert.equal(easConfig.build[name].env.EXPO_PUBLIC_API_BASE_URL, 'http://81.71.71.31');
  }
});
