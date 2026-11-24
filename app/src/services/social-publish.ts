/**
 * 社区发布的乐观流程（发帖 / 发故事）。
 *
 * 点了「发布」就先把内容放上屏（本地图片预览 + 发布状态），真正的上传与落库
 * 在后台跑；服务端确认后由列表数据接管本地占位。失败时内容留在占位里，可重试，
 * 重试复用同一个幂等键（服务端据此去重，不会重复发布）。
 */

import { createPost, createStory } from '@/services/api';
import {
  nextPendingId,
  useSocialPendingStore,
  type PendingPost,
  type PendingPostInput,
  type PendingStory,
  type PendingStoryInput,
} from '@/stores/social-pending';
import type { Post, Story } from '@/types/social';

export type PendingAuthor = {
  id: number;
  displayName: string;
  avatarEmoji: string;
};

export type PendingPet = {
  id: number;
  name: string;
  emoji: string | null;
} | null;

function errorText(exc: unknown, fallback: string): string {
  // 不依赖 instanceof（跨 realm 时不可靠）：有 message 就用 message
  if (exc && typeof exc === 'object' && 'message' in exc) {
    const message = String((exc as { message?: unknown }).message ?? '').trim();
    if (message) return message;
  }
  return fallback;
}

/** 本地帖子对象：图片先用本地 uri 预览，服务端确认后由服务端那份接管 */
export function buildLocalPost(
  input: PendingPostInput,
  author: PendingAuthor,
  pet: PendingPet,
): Post {
  const first = input.images[0];
  return {
    id: nextPendingId(),
    user_id: author.id,
    display_name: author.displayName,
    avatar_emoji: author.avatarEmoji,
    caption: input.caption,
    location_text: input.location,
    imageUrl: first?.uri ?? '',
    created_at: new Date().toISOString(),
    pet_id: pet?.id ?? null,
    pet_name: pet?.name ?? null,
    pet_emoji: pet?.emoji ?? null,
    likes: 0,
    comments_count: 0,
    liked_by_me: false,
    audience: input.audience,
    media: input.images.map((image) => ({ url: image.uri, kind: 'image' as const })),
    isMine: true,
  };
}

/** 本地故事对象：图片用本地 uri 预览 */
export function buildLocalStory(input: PendingStoryInput, author: PendingAuthor): Story {
  return {
    id: nextPendingId(),
    user_id: author.id,
    display_name: author.displayName,
    avatar_emoji: author.avatarEmoji,
    imageUrl: input.image.uri,
    caption: input.caption,
    audience: input.audience,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 24 * 3600 * 1000).toISOString(),
    viewed: true,
  };
}

/** 建一条本地占位并登记到 store（不发起网络请求） */
export function stagePost(
  input: PendingPostInput,
  mutationId: string,
  author: PendingAuthor,
  pet: PendingPet,
  onConfirmed?: () => void,
): PendingPost {
  const post = buildLocalPost(input, author, pet);
  const entry: PendingPost = {
    id: post.id,
    serverId: null,
    post,
    status: 'publishing',
    error: '',
    input,
    mutationId,
    onConfirmed,
  };
  useSocialPendingStore.getState().addPost(entry);
  return entry;
}

export function stageStory(
  input: PendingStoryInput,
  mutationId: string,
  author: PendingAuthor,
  onConfirmed?: () => void,
): PendingStory {
  const story = buildLocalStory(input, author);
  const entry: PendingStory = {
    id: story.id,
    serverId: null,
    story,
    status: 'publishing',
    error: '',
    input,
    mutationId,
    onConfirmed,
  };
  useSocialPendingStore.getState().addStory(entry);
  return entry;
}

/** 后台上传发布（发帖）：确认后记下服务端 id，列表据此换成本地那份之外的服务端版本 */
export function publishPost(entry: PendingPost): void {
  void createPost({
    images: entry.input.images,
    caption: entry.input.caption,
    location: entry.input.location,
    petId: entry.input.petId,
    audience: entry.input.audience,
    mutationId: entry.mutationId,
  })
    .then((data) => {
      useSocialPendingStore.getState().patchPost(entry.id, {
        status: 'confirmed',
        serverId: data.postId,
        error: '',
        // 换成服务端 id：卡片变成正式帖子（下次列表刷新由服务端那份接管）
        post: { ...entry.post, id: data.postId },
      });
      entry.onConfirmed?.();
    })
    .catch((exc) => {
      useSocialPendingStore
        .getState()
        .patchPost(entry.id, { status: 'failed', error: errorText(exc, '发布失败，请重试') });
    });
}

/** 后台上传发布（发故事） */
export function publishStory(entry: PendingStory): void {
  void createStory({
    image: entry.input.image,
    caption: entry.input.caption,
    audience: entry.input.audience,
    mutationId: entry.mutationId,
  })
    .then((data) => {
      useSocialPendingStore.getState().patchStory(entry.id, {
        status: 'confirmed',
        serverId: data.story.id,
        error: '',
        // 换成服务端那条（图片已是上传后的地址）
        story: data.story,
      });
      entry.onConfirmed?.();
    })
    .catch((exc) => {
      useSocialPendingStore
        .getState()
        .patchStory(entry.id, { status: 'failed', error: errorText(exc, '发布失败，请重试') });
    });
}

/** 重试：复用同一个幂等键，服务端按 clientMutationId 去重 */
export function retryPost(entry: PendingPost): void {
  useSocialPendingStore.getState().patchPost(entry.id, { status: 'publishing', error: '' });
  publishPost(entry);
}

export function retryStory(entry: PendingStory): void {
  useSocialPendingStore.getState().patchStory(entry.id, { status: 'publishing', error: '' });
  publishStory(entry);
}
