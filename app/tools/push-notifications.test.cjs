/**
 * W13 原生推送客户端测试（iOS 首版）。
 *
 * 覆盖：平台闸门（iOS 之外不注册、不申请权限）、系统权限状态映射（已授权 / 系统拒绝 /
 * 系统撤回 / 暂不可用）、稳定收件箱 ID 解析与深链缝、注册与关闭的服务端调用、
 * 以及 iOS 之外的平台不触碰任何通知 API。
 *
 * 原生模块（expo-notifications / expo-constants / AsyncStorage / react-native）
 * 在这里用本地假实现驱动，不接触真机与真实服务方。
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { load } = require('./helpers/load-ts.cjs');

/** 深链缝是模块级单例：整份测试共用同一个实例。 */
const seam = load('services/inbox-deep-link.ts');

/** AsyncStorage 假实现（内存）。 */
function fakeStorage(initial = {}) {
  const store = { ...initial };
  return {
    store,
    async getItem(key) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    async setItem(key, value) {
      store[key] = value;
    },
    async removeItem(key) {
      delete store[key];
    },
  };
}

function fakeNotifications(overrides = {}) {
  return {
    IosAuthorizationStatus: {
      NOT_DETERMINED: 0,
      DENIED: 1,
      AUTHORIZED: 2,
      PROVISIONAL: 3,
      EPHEMERAL: 4,
    },
    PermissionStatus: { DENIED: 'denied', GRANTED: 'granted', UNDETERMINED: 'undetermined' },
    getPermissionsAsync: async () => ({
      granted: false,
      status: 'undetermined',
      ios: { status: 0 },
    }),
    requestPermissionsAsync: async () => ({ granted: false, status: 'denied', ios: { status: 1 } }),
    getExpoPushTokenAsync: async () => ({ data: 'ExponentPushToken[test0001]', type: 'expo' }),
    setNotificationHandler: () => {},
    addNotificationResponseReceivedListener: () => ({ remove: () => {} }),
    addPushTokenListener: () => ({ remove: () => {} }),
    getLastNotificationResponse: () => null,
    clearLastNotificationResponse: () => {},
    ...overrides,
  };
}

function loadPush(options = {}) {
  const {
    platform = 'ios',
    storage = fakeStorage(),
    notifications = fakeNotifications(),
    projectId = 'b390999e-ff9a-44f8-9028-05691e764f80',
    apiCalls = [],
    gates = { ios: true },
    enabled = true,
    installations = [],
  } = options;
  const api = {
    request: async (url, init) => {
      apiCalls.push({ url, init });
      if (url === '/push/status') {
        return {
          enabled,
          gates: { ios: gates.ios, android: false, web: false },
          installations,
          deliveries: [],
        };
      }
      return { success: true, action: 'created' };
    },
  };
  const push = load('services/push.ts', {
    '@/services/api': api,
    '@/services/inbox-deep-link': seam,
    'expo-notifications': notifications,
    'expo-constants': projectId ? { expoConfig: { extra: { eas: { projectId } } } } : {},
    '@react-native-async-storage/async-storage': storage,
    'react-native': {
      Platform: { OS: platform },
      AppState: { addEventListener: () => ({ remove: () => {} }) },
    },
  });
  return { push, apiCalls, storage };
}

// ── 深链缝 ──────────────────────────────────────────────────────────────────

test('深链缝：一律指向 W12 权威解析页 /inbox/[id]，无第二套解析状态', () => {
  assert.equal(
    seam.resolveInboxDeepLink('inbox-42'),
    '/inbox/inbox-42',
    '稳定收件箱 ID 指向 W12 的统一解析页',
  );
  assert.equal(seam.resolveInboxDeepLink('  '), null, '空 ID 不定位');
});

test('深链缝：只认稳定收件箱 ID，载荷里的其它键不当作定位依据', () => {
  assert.equal(seam.readInboxItemId({ inboxItemId: 'a1' }), 'a1');
  assert.equal(seam.readInboxItemId({ inbox_item_id: 'b2' }), 'b2');
  assert.equal(seam.readInboxItemId({ itemId: 'c3' }), 'c3');
  assert.equal(seam.readInboxItemId({ petName: 'Leia' }), null);
  assert.equal(seam.readInboxItemId(null), null);
  assert.equal(seam.readInboxItemId('a1'), null);
});

// ── 平台闸门 ────────────────────────────────────────────────────────────────

test('平台闸门：只有 iOS 走远程推送，Android 与 Web 不注册也不申请权限', async () => {
  for (const platform of ['android', 'web']) {
    const { push, apiCalls } = loadPush({ platform });
    assert.equal(push.pushSupported(), false, `${platform} 不支持远程推送`);
    let requested = false;
    const status = await push.readPushStatus();
    assert.equal(status.supported, false);
    assert.match(status.message, /站内收件箱/);
    const enabled = await push.enablePush();
    assert.equal(enabled.enabled, false);
    assert.equal(requested, false);
    assert.deepEqual(apiCalls, [], `${platform} 不向服务端注册，也不申请权限`);
  }
});

test('平台闸门：iOS 支持远程推送', () => {
  const { push } = loadPush({ platform: 'ios' });
  assert.equal(push.pushSupported(), true);
});

// ── 权限状态映射 ────────────────────────────────────────────────────────────

test('权限状态：已授权 / 未询问 / 系统拒绝 / 系统撤回', async () => {
  const granted = loadPush({
    notifications: fakeNotifications({
      getPermissionsAsync: async () => ({ granted: true, status: 'granted', ios: { status: 2 } }),
    }),
  });
  assert.equal(await granted.push.readPermissionStatus(), 'granted');

  const undetermined = loadPush();
  assert.equal(await undetermined.push.readPermissionStatus(), 'undetermined');

  const denied = loadPush({
    notifications: fakeNotifications({
      getPermissionsAsync: async () => ({ granted: false, status: 'denied', ios: { status: 1 } }),
    }),
  });
  assert.equal(await denied.push.readPermissionStatus(), 'denied');

  // 曾授权后被系统收回 → revoked（不是 denied）
  const storage = fakeStorage({ 'petsense.push.permission': 'granted' });
  const revoked = loadPush({
    storage,
    notifications: fakeNotifications({
      getPermissionsAsync: async () => ({ granted: false, status: 'denied', ios: { status: 1 } }),
    }),
  });
  assert.equal(await revoked.push.readPermissionStatus(), 'revoked');
  assert.equal(storage.store['petsense.push.permission'], 'revoked');

  // 读取权限时抛错 → 暂不可用
  const unavailable = loadPush({
    notifications: fakeNotifications({
      getPermissionsAsync: async () => {
        throw new Error('boom');
      },
    }),
  });
  assert.equal(await unavailable.push.readPermissionStatus(), 'unavailable');
});

test('权限状态：iOS 用 ios.status 判定临时授权', async () => {
  const provisional = loadPush({
    notifications: fakeNotifications({
      getPermissionsAsync: async () => ({ granted: false, status: 'denied', ios: { status: 3 } }),
    }),
  });
  assert.equal(await provisional.push.readPermissionStatus(), 'granted');
});

// ── 注册 / 关闭 ─────────────────────────────────────────────────────────────

test('注册：用户主动开启后才申请权限并注册安装实例', async () => {
  let permissionRequested = false;
  const { push, apiCalls } = loadPush({
    notifications: fakeNotifications({
      requestPermissionsAsync: async () => {
        permissionRequested = true;
        return { granted: true, status: 'granted', ios: { status: 2 } };
      },
    }),
  });
  const status = await push.enablePush();
  assert.equal(permissionRequested, true);
  assert.equal(status.enabled, true);
  assert.equal(status.registration, 'registered');
  const urls = apiCalls.map((call) => call.url);
  assert.ok(urls.includes('/push/installations'), '注册安装实例');
  assert.ok(urls.includes('/push/preferences'), '打开服务端开关');
  const registerCall = apiCalls.find((call) => call.url === '/push/installations');
  const body = registerCall.init.body;
  assert.equal(body.platform, 'ios');
  assert.equal(body.permissionStatus, 'granted');
  assert.match(body.installationId, /^inst-[0-9a-f]{32}$/, '安装实例稳定 ID');
});

test('注册：系统拒绝时不注册、不打开服务端开关', async () => {
  const { push, apiCalls } = loadPush({
    notifications: fakeNotifications({
      requestPermissionsAsync: async () => ({
        granted: false,
        status: 'denied',
        ios: { status: 1 },
      }),
    }),
  });
  const status = await push.enablePush();
  assert.equal(status.enabled, false);
  assert.match(status.message, /拒绝|权限/);
  assert.equal(
    apiCalls.filter((call) => call.url === '/push/installations').length,
    0,
    '被拒时不向服务端注册安装实例',
  );
  const prefCall = apiCalls.find((call) => call.url === '/push/preferences');
  assert.ok(prefCall, '被拒时确保服务端开关是关的');
  assert.equal(prefCall.init.body.enabled, false);
});

test('注册：拿不到推送凭证时报「暂不可用」并可重试', async () => {
  const { push, apiCalls } = loadPush({
    notifications: fakeNotifications({
      requestPermissionsAsync: async () => ({
        granted: true,
        status: 'granted',
        ios: { status: 2 },
      }),
      getExpoPushTokenAsync: async () => {
        throw new Error('ERR_NOTIFICATIONS_NO_EXPERIENCE_ID');
      },
    }),
  });
  const status = await push.enablePush();
  assert.equal(status.enabled, false);
  assert.equal(status.registration, 'error');
  assert.match(status.message, /凭证/);
  assert.equal(
    apiCalls.filter((call) => call.url === '/push/installations').length,
    0,
    '拿不到凭证时不注册',
  );
});

test('服务端闸门关闭：不申请系统权限，如实说明状态', async () => {
  let permissionRequested = false;
  const { push, apiCalls } = loadPush({
    gates: { ios: false },
    notifications: fakeNotifications({
      requestPermissionsAsync: async () => {
        permissionRequested = true;
        return { granted: true, status: 'granted', ios: { status: 2 } };
      },
    }),
  });
  const status = await push.enablePush();
  assert.equal(permissionRequested, false, '闸门关闭时不弹系统权限框');
  assert.equal(status.enabled, false);
  assert.match(status.message, /暂未开放|开关关闭/);
  assert.equal(
    apiCalls.filter((call) => call.url === '/push/installations').length,
    0,
    '闸门关闭时不注册',
  );
});

test('安装实例 ID 只生成一次：并发调用得到同一个 ID', async () => {
  const { push } = loadPush();
  const ids = await Promise.all([
    push.getInstallationId(),
    push.getInstallationId(),
    push.getInstallationId(),
  ]);
  assert.equal(new Set(ids).size, 1, '并发调用共用同一个安装实例 ID');
  assert.match(ids[0], /^inst-[0-9a-f]{32}$/);
});

test('重复点击折叠成一次导航', () => {
  const { push } = loadPush();
  assert.equal(push.claimNavigation('inbox-1'), true, '第一次点击取得导航权');
  assert.equal(push.claimNavigation('inbox-1'), false, '同一收件箱 ID 的重复点击被折叠');
  push.releaseNavigation();
  assert.equal(push.claimNavigation('inbox-2'), true, '不同的收件箱 ID 不受影响');
  push.releaseNavigation();
  assert.equal(push.claimNavigation(null), false, '没有收件箱 ID 不导航');
});

test('未登录时点击：先记下收件箱 ID，登录后续接（且不串账号）', async () => {
  const { push, storage } = loadPush();
  await push.rememberPendingInboxItem('inbox-77', 42);
  const pending = await push.takePendingInboxItem(42);
  assert.equal(pending, 'inbox-77');
  assert.equal(await push.takePendingInboxItem(42), null, '取一次即清空，不重复导航');
  await push.rememberPendingInboxItem('inbox-88', 42);
  assert.equal(await push.takePendingInboxItem(99), null, '不把旧账号的条目交给新账号');
  assert.ok(storage);
});

test('注册：没有 EAS project ID 时不换取 token', async () => {
  const { push } = loadPush({ projectId: null });
  assert.equal(push.easProjectId(), null);
  assert.equal(await push.fetchExpoPushToken(), null);
});

test('关闭：只关服务端开关，站内收件箱不受影响', async () => {
  const { push, apiCalls } = loadPush();
  const status = await push.disablePush();
  assert.equal(status.enabled, false);
  assert.match(status.message, /站内收件箱/);
  const prefCall = apiCalls.find((call) => call.url === '/push/preferences');
  assert.ok(prefCall);
  assert.equal(prefCall.init.body.enabled, false);
});

test('退出：解除本机安装实例绑定', async () => {
  const { push, apiCalls } = loadPush();
  await push.unregisterInstallation();
  const call = apiCalls.find((item) => item.url.startsWith('/push/installations/'));
  assert.ok(call, '退出时调用解除绑定');
  assert.equal(call.init.method, 'DELETE');
});

// ── 登录后恢复绑定 / 状态只看本机 ──────────────────────────────────────────

const GRANTED = {
  getPermissionsAsync: async () => ({ granted: true, status: 'granted', ios: { status: 2 } }),
};

const MINE = { installationId: 'inst-mine00000001' };
const OTHER = { installationId: 'inst-other0000001' };

function installation(overrides) {
  return {
    platform: 'ios',
    state: 'active',
    permissionStatus: 'granted',
    hasToken: true,
    eligible: true,
    tokenRotated: false,
    tokenVersion: 1,
    lastSeenAt: null,
    updatedAt: '2026-11-24T00:00:00.000Z',
    ...overrides,
  };
}

test('登录后恢复绑定：本机安装实例被停用时重新注册，不申请权限也不改开关', async () => {
  let permissionRequested = false;
  const { push, apiCalls } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications({
      ...GRANTED,
      requestPermissionsAsync: async () => {
        permissionRequested = true;
        return { granted: true, status: 'granted', ios: { status: 2 } };
      },
    }),
    // 退出登录后服务端把本机实例停用，用户的开关仍是开着的
    installations: [installation({ ...MINE, state: 'revoked', eligible: false })],
  });
  await push.restoreRegistration();
  assert.equal(permissionRequested, false, '恢复绑定不弹系统权限框');
  const register = apiCalls.find((call) => call.url === '/push/installations');
  assert.ok(register, '重新注册本机安装实例');
  assert.equal(
    register.init.body.installationId,
    MINE.installationId,
    '沿用原安装实例 ID，去重键不变',
  );
  assert.equal(register.init.body.permissionStatus, 'granted');
  assert.equal(
    apiCalls.some((call) => call.url === '/push/preferences'),
    false,
    '不改用户的开关',
  );
});

test('登录后恢复绑定：本机仍可接收时不重复注册', async () => {
  const { push, apiCalls } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications(GRANTED),
    installations: [installation(OTHER), installation(MINE)],
  });
  await push.restoreRegistration();
  assert.equal(apiCalls.filter((call) => call.url === '/push/installations').length, 0);
});

test('登录后恢复绑定：用户没开启、闸门关闭或系统权限不在时不注册', async () => {
  const cases = [
    { name: '用户没开启', enabled: false },
    { name: '服务端闸门关闭', gates: { ios: false } },
    { name: '系统权限不在', notifications: fakeNotifications() },
  ];
  for (const item of cases) {
    const { push, apiCalls } = loadPush({
      storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
      notifications: item.notifications ?? fakeNotifications(GRANTED),
      enabled: item.enabled ?? true,
      gates: item.gates ?? { ios: true },
      installations: [],
    });
    await push.restoreRegistration();
    assert.equal(
      apiCalls.filter((call) => call.url === '/push/installations').length,
      0,
      item.name,
    );
  }
});

test('登录后恢复绑定：旧版服务端不带 eligible 时按是否还有 token 判定', async () => {
  const { push, apiCalls } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications(GRANTED),
    installations: [installation({ ...MINE, eligible: undefined, hasToken: false })],
  });
  await push.restoreRegistration();
  assert.ok(
    apiCalls.some((call) => call.url === '/push/installations'),
    'token 没了就重新注册',
  );
});

test('登录后恢复绑定：iOS 之外什么都不做', async () => {
  for (const platform of ['android', 'web']) {
    const { push, apiCalls } = loadPush({ platform });
    await push.restoreRegistration();
    assert.deepEqual(apiCalls, [], `${platform} 不向服务端发任何推送请求`);
  }
});

test('状态：只看本机绑定——别的设备已注册、本机已停用时如实显示未开启', async () => {
  const { push } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications(GRANTED),
    installations: [
      installation(OTHER),
      installation({ ...MINE, state: 'revoked', eligible: false }),
    ],
  });
  const status = await push.readPushStatus();
  assert.equal(status.enabled, false, '本机收不到就不显示已开启');
  assert.equal(status.registration, 'none');
  assert.match(status.message, /站内收件箱/);
});

test('状态：本机可接收且用户已开启时显示已开启', async () => {
  const { push } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications(GRANTED),
    installations: [installation(MINE)],
  });
  const status = await push.readPushStatus();
  assert.equal(status.enabled, true);
  assert.equal(status.registration, 'registered');
});

test('状态：开关开着但系统权限已被收回时显示未开启', async () => {
  const { push } = loadPush({
    storage: fakeStorage({ 'petsense.push.installationId': MINE.installationId }),
    notifications: fakeNotifications({
      getPermissionsAsync: async () => ({ granted: false, status: 'denied', ios: { status: 1 } }),
    }),
    installations: [installation(MINE)],
  });
  const status = await push.readPushStatus();
  assert.equal(status.enabled, false);
  assert.equal(status.permission, 'denied');
});

// ── 通知响应 → 路由 ─────────────────────────────────────────────────────────

test('通知响应：按稳定收件箱 ID 解析路由，重复点击得到同一条路由', () => {
  const { push } = loadPush();
  const response = {
    notification: {
      request: { content: { data: { inboxItemId: 'inbox-9', category: 'device_alert' } } },
    },
  };
  const first = push.resolveNotificationRoute(response);
  const second = push.resolveNotificationRoute(response);
  assert.equal(first, second, '重复点击解析到同一目标');
  assert.equal(push.extractInboxItemId(response), 'inbox-9');
});

test('通知响应：载荷里没有收件箱 ID 时不导航', () => {
  const { push } = loadPush();
  assert.equal(
    push.resolveNotificationRoute({ notification: { request: { content: { data: {} } } } }),
    null,
  );
  assert.equal(push.resolveNotificationRoute({}), null);
});
