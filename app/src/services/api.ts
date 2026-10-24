/**
 * App 后端 REST 客户端（/api/app/*）。token 放 AsyncStorage，请求头 X-Petsense-Session 携带。
 * 图片路径是相对路径（/uploads/...），页面拼接 absoluteUrl 使用。
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { API_BASE_URL } from '@/config';
import { buildUploadForm } from '@/services/form-upload';
import type {
  AppUser,
  DashboardData,
  Post,
  PostComment,
  SocialPet,
  FollowListUser,
  TrendPeriod,
  TrendsResponse,
  UserProfile,
} from '@/types/social';

const TOKEN_KEY = 'petsense.session.token';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** 内存态 token：写入即时可见，消除「登录后下一个请求读到空存储」的竞态 */
let memoryToken: string | null = null;

export async function loadToken(): Promise<string | null> {
  if (memoryToken) return memoryToken;
  try {
    memoryToken = await AsyncStorage.getItem(TOKEN_KEY);
  } catch {
    memoryToken = null;
  }
  return memoryToken;
}

/** 同步取内存态 token（WS 连接拼 query 参数用；启动早期可能为 null） */
export function getToken(): string | null {
  return memoryToken;
}

export async function saveToken(token: string | null) {
  memoryToken = token;
  try {
    if (token) {
      await AsyncStorage.setItem(TOKEN_KEY, token);
    } else {
      await AsyncStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    // 存储失败按未登录处理，下次启动重新登录
  }
}

export function absoluteUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path}`;
}

export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData; auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }
  if (options.auth !== false) {
    const token = await loadToken();
    if (token) headers['X-Petsense-Session'] = token;
  }
  const response = await fetch(`${API_BASE_URL}/api/app${path}`, {
    method: options.method ?? 'GET',
    headers,
    body,
  });
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === 'object' && 'message' in payload
        ? String((payload as { message: unknown }).message)
        : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return payload as T;
}

// ─── 认证 ───

export async function register(input: {
  username: string;
  displayName: string;
  password: string;
  avatarEmoji: string;
}): Promise<{ user: AppUser }> {
  const data = await request<{ token: string; user: AppUser }>('/auth/register', {
    method: 'POST',
    body: {
      username: input.username,
      display_name: input.displayName,
      password: input.password,
      avatarEmoji: input.avatarEmoji,
    },
  });
  await saveToken(data.token);
  return { user: data.user };
}

export async function login(input: {
  username: string;
  password: string;
}): Promise<{ user: AppUser }> {
  const data = await request<{ token: string; user: AppUser }>('/auth/login', {
    method: 'POST',
    body: { username: input.username, password: input.password },
  });
  await saveToken(data.token);
  return { user: data.user };
}

export async function logout() {
  try {
    await request('/auth/logout', { method: 'POST' });
  } catch {
    // token 已失效也直接清本地
  }
  await saveToken(null);
}

export async function fetchMe(): Promise<AppUser | null> {
  const data = await request<{ user: AppUser | null }>('/auth/me');
  return data.user;
}

// ─── 宠物与 Dashboard ───

export function fetchPets(): Promise<{ pets: SocialPet[] }> {
  return request('/pets');
}

// ─── 设备（一人一设备，认领即绑定宠物） ───

export interface MyDevice {
  deviceId: string;
  name: string;
  claimedAt: string;
  lastSeenAt: string | null;
  petId: number | null;
  petName: string | null;
  online: boolean;
  batteryPercent: number | null;
}

export function fetchMyDevice(): Promise<{ device: MyDevice | null }> {
  return request('/device');
}

export function claimDevice(deviceId: string, name?: string): Promise<{ device: MyDevice }> {
  return request('/device/claim', { method: 'POST', body: { deviceId, name: name ?? '' } });
}

export function releaseDevice(deviceId: string): Promise<{ success: boolean }> {
  return request('/device/release', { method: 'POST', body: { deviceId } });
}

export function createPet(input: {
  name: string;
  species: string;
  breed: string;
  avatarEmoji: string;
  deviceId: string;
}): Promise<{ pet: { id: number; name: string } }> {
  return request('/pets', { method: 'POST', body: input });
}

export function fetchDashboard(petId?: number): Promise<DashboardData> {
  const query = petId ? `?pet_id=${petId}` : '';
  return request(`/dashboard${query}`);
}

// ─── 社区 ───

export function fetchFeed(userId?: number): Promise<{ posts: Post[] }> {
  const query = userId ? `?user_id=${userId}` : '';
  return request(`/feed${query}`);
}

export async function createPost(input: {
  image: { uri: string; name: string; type: string };
  caption: string;
  location: string;
  petId?: number;
}): Promise<{ postId: number }> {
  const data = await buildUploadForm(
    {
      caption: input.caption,
      location: input.location,
      ...(input.petId ? { pet_id: String(input.petId) } : {}),
    },
    [{ field: 'image', ...input.image }],
  );
  return request('/posts', { method: 'POST', form: data });
}

export function toggleLike(postId: number): Promise<{ liked: boolean; likes: number }> {
  return request(`/posts/${postId}/like`, { method: 'POST' });
}

export function fetchComments(postId: number): Promise<{ comments: PostComment[] }> {
  return request(`/posts/${postId}/comments`);
}

export function addComment(postId: number, body: string): Promise<{ success: boolean }> {
  return request(`/posts/${postId}/comments`, { method: 'POST', body: { body } });
}

export function fetchUserProfile(userId: number): Promise<{ profile: UserProfile }> {
  return request(`/users/${userId}`);
}

export function toggleFollow(userId: number): Promise<{ following: boolean }> {
  return request(`/users/${userId}/follow`, { method: 'POST' });
}

// ─── 社区补充 ───

export function deletePost(postId: number): Promise<{ success: boolean }> {
  return request(`/posts/${postId}`, { method: 'DELETE' });
}

export function fetchFollowList(
  userId: number,
  kind: 'followers' | 'following',
): Promise<{ users: FollowListUser[] }> {
  return request(`/users/${userId}/${kind}`);
}

// ─── 趋势统计 ───

export function fetchTrends(period: TrendPeriod): Promise<TrendsResponse> {
  return request(`/trends?period=${period}`);
}
