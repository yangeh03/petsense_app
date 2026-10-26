/**
 * App 后端 REST 客户端（/api/app/*）。token 放 AsyncStorage，请求头 X-Petsense-Session 携带。
 * 图片路径是相对路径（/uploads/...），页面拼接 absoluteUrl 使用。
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { API_BASE_URL } from '@/config';
import {
  buildUploadForm,
  IS_WEB_LIKE,
  sendMultipart,
  type UploadFile,
} from '@/services/form-upload';
import type { PetUpdate } from '@/services/pet-profile';
import type {
  AppUser,
  DashboardData,
  Post,
  PostComment,
  PetAvatarTemplate,
  SocialPet,
  FollowListUser,
  TrendPeriod,
  TrendsResponse,
  UserProfile,
  Page,
  Story,
  StoryGroup,
  SocialNotification,
  NearbyUser,
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

export function isProtectedSocialMedia(path: string): boolean {
  return path.startsWith('/api/app/posts/') || path.startsWith('/api/app/stories/');
}

export function socialImageSource(path: string) {
  const token = getToken();
  return {
    uri: absoluteUrl(path),
    ...(isProtectedSocialMedia(path) && token ? { headers: { 'X-Petsense-Session': token } } : {}),
  };
}

export async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    form?: FormData;
    auth?: boolean;
    signal?: AbortSignal;
    headers?: Record<string, string>;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { ...options.headers };
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
    signal: options.signal,
  });
  if (options.signal?.aborted) throw new Error('请求已取消');
  return parseApiResponse<T>(response.status, await response.text());
}

/** 统一把原始响应转成载荷：非 2xx 抛出带服务端 message 的 ApiError。 */
async function parseApiResponse<T>(status: number, body: string): Promise<T> {
  let payload: unknown = null;
  try {
    payload = body ? JSON.parse(body) : null;
  } catch {
    payload = null;
  }
  const ok = status >= 200 && status < 300;
  if (!ok) {
    const message =
      payload && typeof payload === 'object' && 'message' in payload
        ? String((payload as { message: unknown }).message)
        : `请求失败（${status}）`;
    throw new ApiError(message, status);
  }
  if (
    payload &&
    typeof payload === 'object' &&
    'success' in payload &&
    (payload as { success: unknown }).success === false
  ) {
    throw new ApiError(String((payload as { message?: unknown }).message ?? '请求失败'), status);
  }
  return payload as T;
}

/**
 * 带文件的上传：Web 走 fetch + FormData（Blob），原生走 XHR multipart。
 * 两种路径字段与文件一致，服务端按同一套 multipart 解析。
 */
async function uploadRequest<T>(
  path: string,
  init: { method?: string; fields: Record<string, string>; files: UploadFile[] },
): Promise<T> {
  if (IS_WEB_LIKE) {
    const form = await buildUploadForm(init.fields, init.files);
    return request<T>(path, { method: init.method, form });
  }
  const headers: Record<string, string> = {};
  const token = await loadToken();
  if (token) headers['X-Petsense-Session'] = token;
  const { status, body } = await sendMultipart(`${API_BASE_URL}/api/app${path}`, {
    method: init.method,
    headers,
    fields: init.fields,
    files: init.files,
  });
  return parseApiResponse<T>(status, body);
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

export function fetchAvatarTemplates(): Promise<{ templates: PetAvatarTemplate[] }> {
  return request('/petgif/avatar-templates');
}

export function fetchPet(petId: number): Promise<{ pet: SocialPet }> {
  return request(`/pets/${petId}`);
}

export async function updatePet(petId: number, input: PetUpdate): Promise<{ pet: SocialPet }> {
  const { photo, ...fields } = input;
  if (!photo) return request(`/pets/${petId}`, { method: 'PATCH', body: fields });
  return uploadRequest(`/pets/${petId}`, {
    method: 'PATCH',
    fields: Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value ?? ''])),
    files: [{ field: 'photo', ...photo }],
  });
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

export function fetchDashboard(petId?: number, signal?: AbortSignal): Promise<DashboardData> {
  const query = petId ? `?pet_id=${petId}` : '';
  return request(`/dashboard${query}`, { signal });
}

// ─── 社区 ───

export function fetchFeed(
  userId?: number,
  options?: {
    cursor?: string;
    limit?: number;
    scope?: 'discover' | 'following';
    signal?: AbortSignal;
  },
): Promise<{ posts: Post[]; page: { hasMore: boolean; nextCursor: string | null } }> {
  const params = new URLSearchParams();
  if (userId) params.set('user_id', String(userId));
  if (options?.cursor) params.set('cursor', options.cursor);
  if (options?.limit) params.set('limit', String(options.limit));
  if (options?.scope) params.set('scope', options.scope);
  return request(`/feed${params.size ? `?${params.toString()}` : ''}`, { signal: options?.signal });
}

export function fetchPost(postId: number): Promise<{ post: Post }> {
  return request(`/posts/${postId}`);
}

export function editPost(
  postId: number,
  input: {
    caption: string;
    location?: string;
    audience: 'public' | 'followers';
    version: number;
  },
): Promise<{ post: Post }> {
  return request(`/posts/${postId}`, { method: 'PATCH', body: input });
}

export async function createPost(input: {
  image?: { uri: string; name: string; type: string };
  images?: { uri: string; name: string; type: string }[];
  caption: string;
  location?: string;
  petId?: number;
  audience?: 'public' | 'followers';
  mutationId?: string;
}): Promise<{ postId: number }> {
  const images = input.images ?? (input.image ? [input.image] : []);
  return uploadRequest('/posts', {
    method: 'POST',
    fields: {
      caption: input.caption,
      location: input.location ?? '',
      audience: input.audience ?? 'public',
      ...(input.mutationId ? { client_mutation_id: input.mutationId } : {}),
      ...(input.petId ? { pet_id: String(input.petId) } : {}),
    },
    files: images.map((image) => ({ field: 'images', ...image })),
  });
}

export function toggleLike(postId: number): Promise<{ liked: boolean; likes: number }> {
  return request(`/posts/${postId}/like`, { method: 'POST' });
}

export function fetchComments(
  postId: number,
  cursor?: string,
): Promise<{ comments: PostComment[]; page: Page }> {
  return request(
    `/posts/${postId}/comments${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
  );
}

export function addComment(postId: number, body: string): Promise<{ success: boolean }> {
  return request(`/posts/${postId}/comments`, { method: 'POST', body: { body } });
}

export function addReply(
  postId: number,
  body: string,
  mutationId: string,
  replyToCommentId?: number,
): Promise<{ comment: PostComment }> {
  return request(`/posts/${postId}/comments`, {
    method: 'POST',
    body: { body, clientMutationId: mutationId, replyToCommentId },
  });
}

export function fetchReplies(
  rootId: number,
  cursor?: string,
): Promise<{ comments: PostComment[]; page: Page }> {
  return request(
    `/comments/${rootId}/replies${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
  );
}

export function deleteComment(commentId: number): Promise<{ success: boolean }> {
  return request(`/comments/${commentId}`, { method: 'DELETE' });
}

export function setLike(
  postId: number,
  liked: boolean,
): Promise<{ liked: boolean; likes: number }> {
  return request(`/posts/${postId}/like`, { method: liked ? 'PUT' : 'DELETE' });
}

export function setFollow(userId: number, following: boolean): Promise<{ following: boolean }> {
  return request(`/users/${userId}/follow`, { method: following ? 'PUT' : 'DELETE' });
}

export function setBookmark(postId: number, bookmarked: boolean): Promise<{ bookmarked: boolean }> {
  return request(`/posts/${postId}/bookmark`, { method: bookmarked ? 'PUT' : 'DELETE' });
}

export function fetchBookmarks(cursor?: string): Promise<{ posts: Post[]; page: Page }> {
  return request(`/me/bookmarks${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
}

export function searchCommunity(
  q: string,
  kind: 'all' | 'posts' | 'users' | 'places',
  signal?: AbortSignal,
  cursor?: string,
  place?: string,
): Promise<{
  posts: Post[];
  users: FollowListUser[];
  places: { name: string; postsCount: number }[];
  page: Page;
}> {
  return request(
    `/search?q=${encodeURIComponent(q)}&type=${kind}${
      cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''
    }${place ? `&place=${encodeURIComponent(place)}` : ''}`,
    { signal },
  );
}

export function fetchStoryTray(): Promise<{ groups: StoryGroup[] }> {
  return request('/stories/tray');
}

export function fetchUserStories(userId: number): Promise<{ stories: Story[] }> {
  return request(`/users/${userId}/stories`);
}

export function markStoryViewed(storyId: number): Promise<{ success: boolean }> {
  return request(`/stories/${storyId}/view`, { method: 'PUT' });
}

export function deleteStory(storyId: number): Promise<{ success: boolean }> {
  return request(`/stories/${storyId}`, { method: 'DELETE' });
}

export function fetchStoryViewers(storyId: number): Promise<{
  viewers: {
    id: number;
    display_name: string;
    avatar_emoji: string;
    viewed_at: string;
  }[];
}> {
  return request(`/stories/${storyId}/viewers`);
}

export function createStory(input: {
  image: { uri: string; name: string; type: string };
  caption: string;
  audience: 'public' | 'followers';
  mutationId: string;
}): Promise<{ story: Story }> {
  return uploadRequest('/stories', {
    method: 'POST',
    fields: {
      caption: input.caption,
      audience: input.audience,
      client_mutation_id: input.mutationId,
    },
    files: [{ field: 'image', ...input.image }],
  });
}

export function fetchSocialNotifications(cursor?: string): Promise<{
  notifications: SocialNotification[];
  unreadCount: number;
  page: Page;
}> {
  return request(`/notifications${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
}

export function markNotificationRead(id: number): Promise<{ unreadCount: number }> {
  return request(`/notifications/${id}/read`, { method: 'PUT' });
}

export function markNotificationsReadThrough(
  highWaterId: number,
): Promise<{ unreadCount: number }> {
  return request('/notifications/read-through', { method: 'PUT', body: { highWaterId } });
}

export function fetchDiscoveryPreference(): Promise<{
  enabled: boolean;
  expiresAt: string | null;
}> {
  return request('/me/discovery-location');
}

export function updateDiscoveryLocation(input: {
  latitude: number;
  longitude: number;
  accuracyMeters: number;
  enabled: boolean;
}): Promise<{ enabled: boolean; expiresAt: string }> {
  return request('/me/discovery-location', { method: 'PUT', body: input });
}

export function disableDiscoveryLocation(): Promise<{ enabled: boolean }> {
  return request('/me/discovery-location', { method: 'DELETE' });
}

export function createNearbyQuery(input: {
  latitude: number;
  longitude: number;
  accuracyMeters: number;
  radiusKm: 1 | 5 | 10 | 20;
}): Promise<{ queryId: string; expiresAt: string }> {
  return request('/nearby/queries', { method: 'POST', body: input });
}

export function fetchNearbyUsers(
  queryId: string,
  cursor?: string,
): Promise<{ users: NearbyUser[]; page: Page }> {
  const params = new URLSearchParams({ queryId });
  if (cursor) params.set('cursor', cursor);
  return request(`/nearby/users?${params.toString()}`);
}

export function fetchNearbyPosts(
  queryId: string,
  cursor?: string,
): Promise<{ posts: Post[]; page: Page }> {
  const params = new URLSearchParams({ queryId });
  if (cursor) params.set('cursor', cursor);
  return request(`/nearby/posts?${params.toString()}`);
}

export function fetchUserProfile(userId: number): Promise<{ profile: UserProfile }> {
  return request(`/users/${userId}`);
}

export function updatePublicProfile(input: {
  displayName: string;
  bio: string;
  avatarEmoji: string;
  version: number;
}): Promise<{ profile: UserProfile }> {
  return request('/me/public-profile', { method: 'PATCH', body: input });
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
  cursor?: string,
): Promise<{ users: FollowListUser[]; page: Page }> {
  return request(
    `/users/${userId}/${kind}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
  );
}

// ─── 趋势统计 ───

export function fetchTrends(period: TrendPeriod): Promise<TrendsResponse> {
  return request(`/trends?period=${period}`);
}
