/**
 * W13 原生推送客户端（首版仅 iOS，Expo SDK 58 expo-notifications + Expo Push Service）。
 *
 * 行为约定（agent-w13-spec.md §客户端与发布）：
 * - **只在用户主动开启时申请系统权限**：应用启动与前台都不弹权限框；设置页里
 *   用户打开开关才请求系统权限并注册。
 * - **本轮只有 iOS 走远程推送**：Android 与 Web 不注册、不申请权限，继续用站内
 *   收件箱（`pushSupported()` 为 false 时本模块不碰任何通知 API）。
 * - **token 只发给本服务端**：客户端不打印 token、凭据或私人内容；服务端也不回传。
 * - **安装实例稳定 ID**：同一安装换账号、退出、token 轮换都按安装实例处理，服务端
 *   据此把旧归属立即从发送集合剔除。
 * - **点击走 W12 统一解析**：通知载荷只带稳定的收件箱 ID，交给
 *   `inbox-deep-link` 解析；未登录时先记下，登录后续接。
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';
import {
  IosAuthorizationStatus,
  PermissionStatus,
  addNotificationResponseReceivedListener,
  addPushTokenListener,
  clearLastNotificationResponse as clearSdkLastNotificationResponse,
  getExpoPushTokenAsync,
  getLastNotificationResponse,
  getPermissionsAsync,
  requestPermissionsAsync,
  setNotificationHandler,
} from 'expo-notifications';
import { AppState, Platform } from 'react-native';

import { request } from '@/services/api';
import { readInboxItemId, resolveInboxDeepLink } from '@/services/inbox-deep-link';

const INSTALLATION_KEY = 'petsense.push.installationId';
const PERMISSION_KEY = 'petsense.push.permission';
const PENDING_ITEM_KEY = 'petsense.push.pendingInboxItemId';

export type PushPermissionStatus =
  'granted' | 'denied' | 'revoked' | 'undetermined' | 'unavailable';

export type PushRegistration = 'none' | 'registered' | 'error';

export type PushStatus = {
  /** 当前构建是否支持远程推送（本轮仅 iOS）。 */
  supported: boolean;
  platform: 'ios' | 'android' | 'web';
  /** 用户是否在设置里开启了推送。 */
  enabled: boolean;
  permission: PushPermissionStatus;
  registration: PushRegistration;
  /** 面向用户的中文说明。 */
  message: string;
};

export type PushDeliveryRecord = {
  id: number;
  inboxItemId: string;
  installationId: string;
  category: string;
  state: string;
  reason: string | null;
  errorCode: string | null;
  attempts: number;
  ticketStatus: string | null;
  receiptStatus: string | null;
  createdAt: string;
  sentAt: string | null;
  finishedAt: string | null;
};

export type PushServerStatus = {
  enabled: boolean;
  gates: { ios: boolean; android: boolean; web: boolean };
  installations: {
    installationId: string;
    platform: string;
    state: string;
    permissionStatus: string;
    hasToken: boolean;
    /** 这台设备此刻能否接收（在用、有 token、系统已授权）。旧版服务端可能不带。 */
    eligible?: boolean;
    tokenRotated: boolean;
    lastSeenAt: string | null;
    updatedAt: string;
  }[];
  deliveries: PushDeliveryRecord[];
};

/** 本轮只有 iOS 支持远程推送；Android 与 Web 保持站内收件箱。 */
export function pushSupported(): boolean {
  return Platform.OS === 'ios';
}

function platformName(): 'ios' | 'android' | 'web' {
  if (Platform.OS === 'ios') return 'ios';
  if (Platform.OS === 'android') return 'android';
  return 'web';
}

/** EAS project ID：取不到时无法换取 Expo token（由服务端开关兜底，不静默失败）。 */
export function easProjectId(): string | null {
  const fromConfig = Constants?.expoConfig?.extra?.eas?.projectId;
  if (typeof fromConfig === 'string' && fromConfig.trim()) return fromConfig.trim();
  const fromEas = Constants?.easConfig?.projectId;
  if (typeof fromEas === 'string' && fromEas.trim()) return fromEas.trim();
  return null;
}

function randomId(): string {
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** 安装实例稳定 ID：首次生成后持久化，同一安装保持不变。 */
let installationIdPromise: Promise<string> | null = null;

export function getInstallationId(): Promise<string> {
  // 并发调用共用同一个 promise：安装实例 ID 只生成一次，不会出现两份绑定
  if (!installationIdPromise) installationIdPromise = resolveInstallationId();
  return installationIdPromise;
}

async function resolveInstallationId(): Promise<string> {
  const existing = await AsyncStorage.getItem(INSTALLATION_KEY);
  if (existing && existing.trim()) return existing.trim();
  const created = `inst-${randomId()}`;
  await AsyncStorage.setItem(INSTALLATION_KEY, created);
  return created;
}

/** 读取上次已知的权限状态（用于区分「系统拒绝」与「系统撤回」）。 */
async function readLastPermission(): Promise<PushPermissionStatus | null> {
  const raw = await AsyncStorage.getItem(PERMISSION_KEY);
  if (raw === 'granted' || raw === 'denied' || raw === 'revoked' || raw === 'undetermined') {
    return raw;
  }
  return null;
}

async function writePermission(status: PushPermissionStatus): Promise<void> {
  try {
    await AsyncStorage.setItem(PERMISSION_KEY, status);
  } catch {
    // 存储失败只影响「撤回 vs 拒绝」的区分，不影响功能
  }
}

/**
 * 读取系统权限状态。
 *
 * iOS 的权限比 Android 细，必须读 `ios.status`（SDK 58 文档口径）。曾授权后被
 * 系统收回 → 'revoked'；用户明确拒绝且不可再问 → 'denied'；从未询问 → 'undetermined'。
 */
export async function readPermissionStatus(): Promise<PushPermissionStatus> {
  if (!pushSupported()) return 'unavailable';
  try {
    const status = await getPermissionsAsync();
    const granted =
      status.granted ||
      status.ios?.status === IosAuthorizationStatus.AUTHORIZED ||
      status.ios?.status === IosAuthorizationStatus.PROVISIONAL;
    if (granted) {
      await writePermission('granted');
      return 'granted';
    }
    const last = await readLastPermission();
    const denied = status.status === PermissionStatus.DENIED;
    if (!denied) {
      await writePermission('undetermined');
      return 'undetermined';
    }
    if (last === 'granted') {
      await writePermission('revoked');
      return 'revoked';
    }
    await writePermission('denied');
    return 'denied';
  } catch {
    await writePermission('unavailable');
    return 'unavailable';
  }
}

/** 请求系统权限（只在用户主动开启时调用）。 */
export async function requestSystemPermission(): Promise<PushPermissionStatus> {
  if (!pushSupported()) return 'unavailable';
  try {
    const result = await requestPermissionsAsync({
      ios: { allowAlert: true, allowBadge: true, allowSound: true },
    });
    const granted =
      result.granted ||
      result.ios?.status === IosAuthorizationStatus.AUTHORIZED ||
      result.ios?.status === IosAuthorizationStatus.PROVISIONAL;
    const status: PushPermissionStatus = granted ? 'granted' : 'denied';
    await writePermission(status);
    return status;
  } catch {
    await writePermission('unavailable');
    return 'unavailable';
  }
}

/** 取 Expo Push Service 的 token（只提交给本服务端，不打印）。 */
export async function fetchExpoPushToken(): Promise<string | null> {
  if (!pushSupported()) return null;
  const projectId = easProjectId();
  if (!projectId) return null;
  try {
    const token = await getExpoPushTokenAsync({ projectId });
    return token.data;
  } catch {
    return null;
  }
}

type PushApi = {
  register: (body: Record<string, unknown>) => Promise<{ action: string }>;
  rotate: (installationId: string, body: Record<string, unknown>) => Promise<unknown>;
  reportPermission: (installationId: string, body: Record<string, unknown>) => Promise<unknown>;
  unregister: (installationId: string) => Promise<unknown>;
  setEnabled: (enabled: boolean) => Promise<unknown>;
  status: () => Promise<PushServerStatus>;
};

/** 服务端接口（集中在一处，便于测试替换）。 */
export const pushApi: PushApi = {
  register: (body) =>
    request('/push/installations', { method: 'POST', body }) as Promise<{ action: string }>,
  rotate: (installationId, body) =>
    request(`/push/installations/${encodeURIComponent(installationId)}/token`, {
      method: 'POST',
      body,
    }) as Promise<unknown>,
  reportPermission: (installationId, body) =>
    request(`/push/installations/${encodeURIComponent(installationId)}/permission`, {
      method: 'POST',
      body,
    }) as Promise<unknown>,
  unregister: (installationId) =>
    request(`/push/installations/${encodeURIComponent(installationId)}`, {
      method: 'DELETE',
    }) as Promise<unknown>,
  setEnabled: (enabled) =>
    request('/push/preferences', { method: 'PUT', body: { enabled } }) as Promise<unknown>,
  status: () => request('/push/status') as Promise<PushServerStatus>,
};

async function reportPermissionToServer(
  installationId: string,
  permission: PushPermissionStatus,
): Promise<void> {
  try {
    await pushApi.reportPermission(installationId, { permissionStatus: permission });
  } catch {
    // 上报失败不阻断本地状态：下次同步会再报
  }
}

/**
 * 用户在设置里开启推送：申请系统权限 → 取 token → 注册安装实例 → 打开服务端开关。
 *
 * 权限被拒时不注册、不打开服务端开关：服务端开关保持关闭即不发送（默认关闭）。
 */
export async function enablePush(): Promise<PushStatus> {
  if (!pushSupported()) {
    return {
      supported: false,
      platform: platformName(),
      enabled: false,
      permission: 'unavailable',
      registration: 'none',
      message: '当前版本仅在 iOS 支持原生推送，Android 与 Web 请继续使用站内收件箱。',
    };
  }
  // 服务端闸门没开就不申请系统权限：不弹权限框，如实说明状态
  let server: PushServerStatus | null = null;
  try {
    server = await pushApi.status();
  } catch {
    server = null;
  }
  if (server && !server.gates?.ios) {
    return {
      supported: true,
      platform: platformName(),
      enabled: false,
      permission: await readPermissionStatus(),
      registration: 'none',
      message: '推送暂未开放（服务端开关关闭）。站内收件箱照常。',
    };
  }
  const permission = await requestSystemPermission();
  if (permission !== 'granted') {
    // 拿不到权限就不能留着服务端的开关打开：否则服务端会以为这台设备可收
    try {
      await pushApi.setEnabled(false);
    } catch {
      // 关不掉时服务端仍有资格复核兜底（权限未授权不会发送）
    }
    const status = await readPushStatus();
    return {
      ...status,
      enabled: false,
      message:
        permission === 'denied'
          ? '系统通知权限已被拒绝。可在「设置 → 通知」里重新允许。站内收件箱不受影响。'
          : '暂时拿不到系统通知权限，稍后可重试。站内收件箱不受影响。',
    };
  }
  const token = await fetchExpoPushToken();
  if (!token) {
    return {
      supported: true,
      platform: platformName(),
      enabled: false,
      permission,
      registration: 'error',
      message: '暂时拿不到推送凭证，稍后会自动重试。站内收件箱不受影响。',
    };
  }
  const installationId = await getInstallationId();
  try {
    await pushApi.register({
      installationId,
      platform: platformName(),
      expoToken: token,
      permissionStatus: permission,
    });
    await pushApi.setEnabled(true);
    return {
      supported: true,
      platform: platformName(),
      enabled: true,
      permission,
      registration: 'registered',
      message: '已开启。关键设备提醒和委托回信会推送到这台设备。',
    };
  } catch {
    return {
      supported: true,
      platform: platformName(),
      enabled: false,
      permission,
      registration: 'error',
      message: '注册失败，请稍后重试。站内收件箱不受影响。',
    };
  }
}

/** 用户在设置里关闭推送：服务端立即停发；站内收件箱不受影响。 */
export async function disablePush(): Promise<PushStatus> {
  if (!pushSupported()) {
    return {
      supported: false,
      platform: platformName(),
      enabled: false,
      permission: 'unavailable',
      registration: 'none',
      message: '当前版本仅在 iOS 支持原生推送，Android 与 Web 请继续使用站内收件箱。',
    };
  }
  try {
    await pushApi.setEnabled(false);
  } catch {
    // 关闭失败时下次同步会再关
  }
  return {
    supported: true,
    platform: platformName(),
    enabled: false,
    permission: await readPermissionStatus(),
    registration: 'none',
    message: '已关闭。站内收件箱仍然照常。',
  };
}

/** 读取当前推送状态（服务端开关 + 本机权限 + 本机绑定 + 最近投递记录）。 */
export async function readPushStatus(): Promise<PushStatus> {
  const platform = platformName();
  if (!pushSupported()) {
    return {
      supported: false,
      platform,
      enabled: false,
      permission: 'unavailable',
      registration: 'none',
      message: '当前版本仅在 iOS 支持原生推送，Android 与 Web 请继续使用站内收件箱。',
    };
  }
  const permission = await readPermissionStatus();
  try {
    const server = await pushApi.status();
    // 只看**本机**这一个安装实例：别的设备是否注册与本机能否收到无关
    const installationId = await getInstallationId();
    const own = server.installations.find((item) => item.installationId === installationId);
    const receivable = Boolean(own && (own.eligible ?? own.hasToken));
    // 开关已开但系统权限没了，或本机绑定已停用（退出登录、token 失效）→ 如实显示为
    // 未开启；服务端同样不会向不可接收的设备发送
    const enabled = Boolean(server.enabled) && permission === 'granted' && receivable;
    return {
      supported: true,
      platform,
      enabled,
      permission,
      registration: receivable ? 'registered' : 'none',
      message: enabled
        ? '已开启。关键设备提醒和委托回信会推送到这台设备。'
        : '未开启。站内收件箱仍然照常。',
    };
  } catch {
    return {
      supported: true,
      platform,
      enabled: false,
      permission,
      registration: 'none',
      message: '暂时读不到推送状态，稍后会自动重试。站内收件箱不受影响。',
    };
  }
}

/** 读取最近投递记录（设置页展示「已发送 / 待重试 / 结果未知」等状态）。 */
export async function readDeliveries(): Promise<PushDeliveryRecord[]> {
  if (!pushSupported()) return [];
  try {
    const server = await pushApi.status();
    return server.deliveries ?? [];
  } catch {
    return [];
  }
}

/** token 轮换：系统重新下发 token 时上报（安装实例 ID 不变，不会重复投递）。 */
export async function syncRotatedToken(token: string): Promise<void> {
  if (!pushSupported()) return;
  const installationId = await getInstallationId();
  try {
    await pushApi.rotate(installationId, {
      expoToken: token,
      permissionStatus: await readPermissionStatus(),
    });
  } catch {
    // 轮换上报失败：下次启动会重新注册
  }
}

/** 退出登录：解除本机绑定，服务端立即把旧归属从发送集合剔除。 */
export async function unregisterInstallation(): Promise<void> {
  if (!pushSupported()) return;
  const installationId = await getInstallationId();
  try {
    await pushApi.unregister(installationId);
  } catch {
    // 退出时网络不可用：服务端仍会因权限/归属复核而停发
  }
}

/** 应用回到前台时同步系统权限（用户可能在系统设置里撤回）。 */
export async function syncPermissionToServer(): Promise<void> {
  if (!pushSupported()) return;
  const permission = await readPermissionStatus();
  const installationId = await getInstallationId();
  await reportPermissionToServer(installationId, permission);
}

/**
 * 登录后恢复本机的推送绑定：已登录、权限允许且用户已开启时注册。
 *
 * 退出登录会停用本机安装实例，而服务端的开关保持开着。重新登录后如果只是读状态，
 * 设置页会显示「已开启」，实际却收不到。所以登录（含带会话的冷启动）时核对一次：
 * 本机安装实例不再可接收就重新注册。从不申请系统权限，也不改动用户的开关。
 */
export async function restoreRegistration(): Promise<void> {
  if (!pushSupported()) return;
  const permission = await readPermissionStatus();
  if (permission !== 'granted') return;
  let server: PushServerStatus | null = null;
  try {
    server = await pushApi.status();
  } catch {
    return; // 读不到状态就不动：下次登录或打开设置页时再核对
  }
  if (!server.enabled || !server.gates?.ios) return;
  const installationId = await getInstallationId();
  const own = server.installations.find((item) => item.installationId === installationId);
  if (own && (own.eligible ?? own.hasToken)) return; // 本机仍可接收
  const token = await fetchExpoPushToken();
  if (!token) return;
  try {
    await pushApi.register({
      installationId,
      platform: platformName(),
      expoToken: token,
      permissionStatus: permission,
    });
  } catch {
    // 恢复失败不影响登录：服务端不会向不可接收的设备发送，设置页会如实显示未开启
  }
}

/**
 * 订阅应用前后台切换：回到前台时同步系统权限（用户可能在系统设置里撤回）。
 * 返回取消订阅函数。
 */
export function startPermissionSync(): () => void {
  if (!pushSupported()) return () => {};
  const subscription = AppState.addEventListener('change', (state) => {
    if (state === 'active') void syncPermissionToServer();
  });
  return () => subscription.remove();
}

/**
 * 前台通知处理：与系统行为对齐——同一次交付只出现一个用户可见通知。
 * 前台收到时不弹横幅、不播放声音（避免与系统横幅重复），只保留通知在列表里。
 */
export function installForegroundHandler(): void {
  if (!pushSupported()) return;
  setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: false,
      shouldShowList: true,
      shouldPlaySound: false,
      shouldSetBadge: false,
    }),
  });
}

/** 记下待处理的收件箱 ID（未登录时先记，登录后续接）。 */
export async function rememberPendingInboxItem(
  inboxItemId: string,
  userId: number | null = null,
): Promise<void> {
  try {
    await AsyncStorage.setItem(
      PENDING_ITEM_KEY,
      JSON.stringify({ userId: userId ?? null, itemId: inboxItemId, at: Date.now() }),
    );
  } catch {
    // 记不下就算了：点击仍会走当次导航
  }
}

/**
 * 取出并清空待处理的收件箱 ID（登录后续接用）。
 *
 * **只接受属于当前账号的待处理项**：换了账号后，旧账号记下的收件箱 ID 不能影响
 * 新账号（否则新主人会被带到旧账号的消息上）。
 */
export async function takePendingInboxItem(userId: number | null = null): Promise<string | null> {
  let raw: string | null = null;
  try {
    raw = await AsyncStorage.getItem(PENDING_ITEM_KEY);
    await AsyncStorage.removeItem(PENDING_ITEM_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { userId?: number | null; itemId?: string };
    if (parsed.userId != null && userId != null && parsed.userId !== userId) {
      return null; // 属于别的账号：丢弃，不影响当前账号
    }
    return parsed.itemId ?? null;
  } catch {
    return null;
  }
}

/** 清掉待处理项（退出登录时调用，避免旧账号的条目串到新账号）。 */
export async function clearPendingInboxItem(): Promise<void> {
  try {
    await AsyncStorage.removeItem(PENDING_ITEM_KEY);
  } catch {
    // 清不掉也不影响：取出时还会按账号再核一次
  }
}

/**
 * 导航去重：同一条收件箱 ID 的重复点击只产生一次应用内导航。
 *
 * 冷启动读取与响应监听器可能对同一次交付各触发一次；这里用「同一 ID 的短时间
 * 窗口 + 全局单航」把重复点击折叠成一次。重复点击也不会重复创建对象或影响未读
 * 语义（未读由 W12 的已读接口负责）。
 */
const NAV_DEDUPE_WINDOW_MS = 2000;
const recentNavigations = new Map<string, number>();
let navigationInFlight = false;

/** 取得本次点击的导航权；没有导航权时返回 false（调用方应跳过导航）。 */
export function claimNavigation(inboxItemId: string | null, now = Date.now()): boolean {
  if (!inboxItemId) return false;
  if (navigationInFlight) return false;
  const last = recentNavigations.get(inboxItemId);
  if (last != null && now - last < NAV_DEDUPE_WINDOW_MS) return false;
  recentNavigations.set(inboxItemId, now);
  navigationInFlight = true;
  return true;
}

/** 导航结束后释放导航权。 */
export function releaseNavigation(): void {
  navigationInFlight = false;
}

/** 从通知响应里取出稳定收件箱 ID。 */
export function extractInboxItemId(response: {
  notification?: { request?: { content?: { data?: unknown } } };
}): string | null {
  return readInboxItemId(response?.notification?.request?.content?.data);
}

/** 通知响应 → 应用内路由（走 W12 统一解析）。 */
export function resolveNotificationRoute(response: {
  notification?: { request?: { content?: { data?: unknown } } };
}): string | null {
  const itemId = extractInboxItemId(response);
  if (!itemId) return null;
  return resolveInboxDeepLink(itemId);
}

/**
 * 订阅通知响应与 token 轮换。返回取消订阅函数。
 *
 * 前台、后台、冷启动三种情形都走同一个回调：取出稳定收件箱 ID → 交给 W12 解析。
 */
export function subscribeNotificationResponses(
  onRoute: (route: string | null, inboxItemId: string | null) => void,
): () => void {
  if (!pushSupported()) return () => {};
  const responseSubscription = addNotificationResponseReceivedListener((response) => {
    const itemId = extractInboxItemId(response);
    onRoute(resolveNotificationRoute(response), itemId);
  });
  const tokenSubscription = addPushTokenListener(({ data }) => {
    if (typeof data === 'string' && data) void syncRotatedToken(data);
  });
  return () => {
    responseSubscription.remove();
    tokenSubscription.remove();
  };
}

/** 冷启动：取最近一次通知响应（App 被通知拉起时用）。 */
export function readLastNotificationRoute(): { route: string | null; inboxItemId: string | null } {
  if (!pushSupported()) return { route: null, inboxItemId: null };
  try {
    const response = getLastNotificationResponse();
    if (!response) return { route: null, inboxItemId: null };
    const itemId = extractInboxItemId(response);
    return { route: resolveNotificationRoute(response), inboxItemId: itemId };
  } catch {
    return { route: null, inboxItemId: null };
  }
}

/** 处理完一次响应后清掉，避免重复点击重复导航。 */
export function clearLastNotificationResponse(): void {
  if (!pushSupported()) return;
  try {
    clearSdkLastNotificationResponse();
  } catch {
    // 清不掉不影响：解析走稳定 ID，重复导航不会重复创建对象
  }
}
