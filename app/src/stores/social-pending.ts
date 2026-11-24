/**
 * 社区发布的乐观状态（发帖 / 发故事）。
 *
 * 服务端的创建接口要等图片上传与落库才返回，主人点了「发布」只能盯着转圈，
 * 自己刚发的东西也迟迟看不到。这里把「已提交、服务端还没确认」的内容先放进
 * 内存 store：列表页立刻渲染本地版本（本地图片预览 + 发布状态），服务端确认
 * 后由列表数据接管（本地占位撤下，避免和服务端那份重复）。
 *
 * 只存在内存里：进程重启后以服务端已保存结果为准（发布页的草稿另有 AsyncStorage 兜底）。
 */

import { create } from 'zustand';
import type { Post, Story } from '@/types/social';

export type PendingStatus = 'publishing' | 'confirmed' | 'failed';

/** 发帖原始入参（重试复用；图片是本地 uri） */
export type PendingPostInput = {
  images: { uri: string; name: string; type: string }[];
  caption: string;
  location: string;
  petId?: number;
  audience: 'public' | 'followers';
};

export type PendingPost = {
  /** 本地 id（负数，与服务端 id 不撞） */
  id: number;
  /** 服务端确认后的帖子 id；未确认为 null */
  serverId: number | null;
  /** 本地构造的帖子（图片用本地 uri 预览） */
  post: Post;
  status: PendingStatus;
  error: string;
  input: PendingPostInput;
  /** 幂等键：重试复用同一个，服务端据此去重 */
  mutationId: string;
  /** 发布成功后清理草稿等收尾动作（进程内回调） */
  onConfirmed?: () => void;
};

/** 发故事原始入参（重试复用；图片是本地 uri） */
export type PendingStoryInput = {
  image: { uri: string; name: string; type: string };
  caption: string;
  audience: 'public' | 'followers';
};

export type PendingStory = {
  id: number;
  serverId: number | null;
  story: Story;
  status: PendingStatus;
  error: string;
  input: PendingStoryInput;
  mutationId: string;
  onConfirmed?: () => void;
};

type SocialPendingState = {
  posts: PendingPost[];
  stories: PendingStory[];
  addPost: (entry: PendingPost) => void;
  patchPost: (id: number, patch: Partial<PendingPost>) => void;
  removePost: (id: number) => void;
  /** 服务端列表已含这些帖子 → 本地占位交回服务端版本 */
  resolvePosts: (serverPostIds: number[]) => void;
  addStory: (entry: PendingStory) => void;
  patchStory: (id: number, patch: Partial<PendingStory>) => void;
  removeStory: (id: number) => void;
  resolveStories: (serverStoryIds: number[]) => void;
};

let seq = 0;

/** 本地占位 id：负数递增，与服务端自增 id 不会撞号 */
export function nextPendingId(): number {
  seq += 1;
  return -seq;
}

export const useSocialPendingStore = create<SocialPendingState>((set) => ({
  posts: [],
  stories: [],
  addPost: (entry) => set((state) => ({ posts: [entry, ...state.posts] })),
  patchPost: (id, patch) =>
    set((state) => ({
      posts: state.posts.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    })),
  removePost: (id) => set((state) => ({ posts: state.posts.filter((item) => item.id !== id) })),
  resolvePosts: (ids) =>
    set((state) => ({
      // 还在上传的（serverId 为 null）继续留着；服务端已收录的撤下本地占位
      posts: state.posts.filter(
        (item) =>
          item.serverId === null || item.status === 'failed' || !ids.includes(item.serverId),
      ),
    })),
  addStory: (entry) => set((state) => ({ stories: [entry, ...state.stories] })),
  patchStory: (id, patch) =>
    set((state) => ({
      stories: state.stories.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    })),
  removeStory: (id) =>
    set((state) => ({ stories: state.stories.filter((item) => item.id !== id) })),
  resolveStories: (ids) =>
    set((state) => ({
      stories: state.stories.filter(
        (item) =>
          item.serverId === null || item.status === 'failed' || !ids.includes(item.serverId),
      ),
    })),
}));
