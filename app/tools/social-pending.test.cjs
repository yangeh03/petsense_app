const assert = require('node:assert/strict');
const { test } = require('node:test');

const { load } = require('./helpers/load-ts.cjs');

/** 起一个内存版 zustand store（和 agent-w14 测试同一手法），返回 store 模块 */
function pendingModule() {
  const create = (initialize) => {
    let state;
    const listeners = new Set();
    const set = (patch) => {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
      listeners.forEach((listener) => listener(state));
    };
    state = initialize(set);
    return {
      getState: () => state,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    };
  };
  return load('stores/social-pending.ts', { zustand: { create } });
}

function loadPublishService(pending, api) {
  return load('services/social-publish.ts', {
    '@/services/api': api,
    '@/stores/social-pending': pending,
  });
}

const author = { id: 7, displayName: '测试主人', avatarEmoji: '🐶' };

test('本地帖子带本地图片预览，等服务端确认后换成服务端 id', async () => {
  const pending = pendingModule();
  const store = pending.useSocialPendingStore;
  const api = {
    createPost: async () => ({ postId: 321 }),
    createStory: async () => ({ story: { id: 654, imageUrl: '/api/app/stories/1.jpg' } }),
  };
  const service = loadPublishService(pending, api);

  const input = {
    images: [{ uri: 'file:///tmp/a.jpg', name: 'a.jpg', type: 'image/jpeg' }],
    caption: '今天很开心',
    location: '广州',
    petId: 3,
    audience: 'public',
  };
  const entry = service.stagePost(input, 'mut-1', author, {
    id: 3,
    name: 'Marni',
    emoji: '🐕',
  });

  // 本地占位：负 id、自己的帖子、本地图片预览
  assert.ok(entry.id < 0);
  assert.equal(entry.post.id, entry.id);
  assert.equal(entry.post.caption, '今天很开心');
  assert.equal(entry.post.imageUrl, 'file:///tmp/a.jpg');
  assert.equal(entry.post.isMine, true);
  assert.equal(entry.post.pet_name, 'Marni');
  assert.equal(store.getState().posts.length, 1);

  service.publishPost(entry);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const confirmed = store.getState().posts[0];
  assert.equal(confirmed.status, 'confirmed');
  assert.equal(confirmed.serverId, 321);
  assert.equal(confirmed.post.id, 321);
});

test('发布失败留占位并给出原因，可重试', async () => {
  const pending = pendingModule();
  const store = pending.useSocialPendingStore;
  let attempts = 0;
  const api = {
    createPost: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('网络不太好');
      return { postId: 999 };
    },
    createStory: async () => ({ story: { id: 1 } }),
  };
  const service = loadPublishService(pending, api);
  const input = {
    images: [],
    caption: '重试一下',
    location: '',
    audience: 'public',
  };
  const entry = service.stagePost(input, 'mut-2', author, null);
  service.publishPost(entry);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(store.getState().posts[0].status, 'failed');
  assert.equal(store.getState().posts[0].error, '网络不太好');

  service.retryPost(entry);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(store.getState().posts[0].status, 'confirmed');
  assert.equal(store.getState().posts[0].serverId, 999);
  // 重试复用同一个幂等键，服务端据此去重
  assert.equal(store.getState().posts[0].mutationId, 'mut-2');
});

test('服务端列表已含该帖时撤下本地占位；还在上传的留着', () => {
  const pending = pendingModule();
  const store = pending.useSocialPendingStore;
  const state = store.getState();
  state.addPost({
    id: -1,
    serverId: null,
    post: { id: -1 },
    status: 'publishing',
    error: '',
    input: { images: [], caption: '', location: '', audience: 'public' },
    mutationId: 'a',
  });
  state.addPost({
    id: -2,
    serverId: 42,
    post: { id: -2 },
    status: 'confirmed',
    error: '',
    input: { images: [], caption: '', location: '', audience: 'public' },
    mutationId: 'b',
  });
  state.resolvePosts([42]);
  // vm 里造的数组原型与测试 realm 不同，比较前先摊平
  const ids = Array.from(store.getState().posts.map((entry) => entry.id));
  assert.deepEqual(ids, [-1]);
});
