/**
 * W16 多会话（docs/agent-w16-spec.md）：会话列表的纯逻辑、store 的帧路由，以及页面/抽屉里
 * 用源码断言固定的关键行为（URL 参数写回、深链带着会话、主题聚焦已移除）。
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { load } = require('./helpers/load-ts.cjs');
const fs = require('node:fs');
const path = require('node:path');
const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

const turns = require('../src/services/agent-chat-turns.ts');
const summary = (id, fields = {}) => ({
  id,
  petId: 3,
  kind: 'chat',
  careKind: null,
  title: `会话 ${id}`,
  titleCustom: false,
  createdAt: `2026-11-20T00:00:0${id}.000Z`,
  updatedAt: `2026-11-20T00:00:0${id}.000Z`,
  lastTurnAt: null,
  turnCount: 0,
  unread: 0,
  preview: null,
  deletedAt: null,
  ...fields,
});
const serverTurn = (id, fields = {}) => ({
  id,
  requestId: `r${id}`,
  text: '',
  speaker: 'assistant',
  readAt: null,
  target: 'auto',
  reply: '【照料助手】好的',
  status: 'complete',
  createdAt: '2026-11-24T10:00:00.000Z',
  conversationId: 1,
  replyTo: null,
  sources: [],
  blocks: null,
  images: [],
  ...fields,
});

function freshStore() {
  const { useAgentChatStore } = load('stores/agent-chat.ts', {
    zustand: require('zustand'),
    '@/services/agent-chat-turns': turns,
  });
  const store = useAgentChatStore.getState();
  store.resetForUser(1);
  store.setPet({ id: 3, name: '豆豆' });
  return useAgentChatStore;
}

test('会话列表：新摘要覆盖旧的，按最近活动倒序，带删除标记的移出', () => {
  let list = turns.mergeConversations([], [summary(1), summary(2), summary(3)]).list;
  assert.deepEqual(
    list.map((item) => item.id),
    [3, 2, 1],
  ); // 都没有回合：按创建时间，新的在前
  list = turns.mergeConversations(
    list,
    summary(1, { lastTurnAt: '2026-11-24T10:00:00.000Z' }),
  ).list;
  assert.deepEqual(
    list.map((item) => item.id),
    [1, 3, 2],
  );
  list = turns.mergeConversations(
    list,
    summary(2, { lastTurnAt: '2026-11-25T10:00:00.000Z' }),
  ).list;
  assert.deepEqual(
    list.map((item) => item.id),
    [2, 1, 3],
  );
  const removed = turns.mergeConversations(list, {
    ...summary(2),
    deletedAt: '2026-11-25T11:00:00.000Z',
  });
  assert.deepEqual(removed.removed, [2]);
  assert.deepEqual(
    removed.list.map((item) => item.id),
    [1, 3],
  );
  // 改名的摘要覆盖标题
  list = turns.mergeConversations(
    removed.list,
    summary(1, { title: '我们的约定', titleCustom: true }),
  ).list;
  assert.equal(list.find((item) => item.id === 1).title, '我们的约定');
});

test('落地规则：本机记住的上次会话还在就打开它，否则取最近活动的第一个', () => {
  const list = [summary(1, { lastTurnAt: '2026-11-24T10:00:00.000Z' }), summary(2), summary(3)];
  assert.equal(turns.landingConversation(list, 3)?.id, 3);
  assert.equal(turns.landingConversation(list, 99)?.id, 1); // 记住的那个已经不在了
  assert.equal(turns.landingConversation(list, null)?.id, 1);
  assert.equal(turns.landingConversation([], null), null);
});

test('排序：空会话排在有内容的会话后面（与服务端口径一致）', () => {
  // 空会话的创建时间更新，按「最近活动」本该排最前，但空会话必须靠后：
  // 否则新建的空会话会盖住有内容的会话，本机没记住上次会话时还会把主人落到空会话里
  const empty = summary(9, { createdAt: '2026-11-25T12:00:00.000Z', lastTurnAt: null });
  const active = summary(1, { lastTurnAt: '2026-11-24T10:00:00.000Z' });
  const list = turns.mergeConversations([], [active, empty]).list;
  assert.deepEqual(
    list.map((item) => item.id),
    [1, 9],
  );
  // 空会话之间仍按创建时间倒序
  const two = turns.mergeConversations(
    [],
    [
      summary(2, { createdAt: '2026-11-20T00:00:02.000Z' }),
      summary(3, { createdAt: '2026-11-20T00:00:03.000Z' }),
    ],
  ).list;
  assert.deepEqual(
    two.map((item) => item.id),
    [3, 2],
  );
});

test('没有随行摘要的回合帧：列表项本地补活动时间与预览，别的会话的新未读回合加一', () => {
  let list = [summary(1), summary(2)];
  const mine = serverTurn(10, { text: '今天走了多久', conversationId: 1 });
  // 正在看的会话：不计未读
  list = turns.noteTurn(list, 1, mine, true, true);
  assert.equal(list.find((item) => item.id === 1).unread, 0);
  assert.equal(list.find((item) => item.id === 1).preview.text, '今天走了多久');
  // 别的会话来的已落定、未读的新回合：未读加一，并按活动时间排到最前
  list = turns.noteTurn(list, 2, serverTurn(11, { text: '', conversationId: 2 }), false, true);
  assert.equal(list[0].id, 2);
  assert.equal(list[0].unread, 1);
  assert.equal(list[0].preview.text, '好的'); // 预览取首段回复，去掉发言人标记
  // 生成中的回合、已读的回合、重复到达的回合都不加未读
  list = turns.noteTurn(
    list,
    2,
    serverTurn(12, { status: 'pending', conversationId: 2 }),
    false,
    true,
  );
  list = turns.noteTurn(
    list,
    2,
    serverTurn(13, { readAt: '2026-11-24T10:00:00.000Z' }),
    false,
    true,
  );
  list = turns.noteTurn(list, 2, serverTurn(11), false, false);
  assert.equal(list[0].unread, 1);
  // 列表里没有这个会话：原样返回，由调用方把列表标记为过期
  assert.equal(turns.noteTurn(list, 7, serverTurn(20, { conversationId: 7 }), false, true), list);
});

test('store：帧按 conversationId 进对应的会话，回合缓存只保留最近打开的几个', () => {
  const useAgentChatStore = freshStore();
  const store = () => useAgentChatStore.getState();
  store().openChat(1);
  store().setActive(1);
  store().setTurns(1, [serverTurn(1)], 1);
  store().openChat(2);
  store().setTurns(2, [serverTurn(5, { conversationId: 2 })], 1);
  // 带会话 id 的回合快照落到它自己的会话，哪怕当前看的是另一个
  store().applyTurn(serverTurn(6, { conversationId: 2 }));
  assert.deepEqual(
    store().chats[2].turns.map((turn) => turn.id),
    [5, 6],
  );
  assert.deepEqual(
    store().chats[1].turns.map((turn) => turn.id),
    [1],
  );
  // 没加载、也不是正在看的会话：不留零散的回合缓存
  store().applyTurn(serverTurn(9, { conversationId: 9 }));
  assert.equal(store().chats[9], undefined);
  // 随行的会话摘要进列表；删掉的是当前会话时当前会话清空，由页面重新落地
  store().upsertConversations([summary(1), summary(2)]);
  assert.deepEqual(
    store().list.map((item) => item.id),
    [2, 1],
  );
  store().upsertConversations({ ...summary(1), deletedAt: '2026-11-25T11:00:00.000Z' });
  assert.equal(store().activeId, null);
  assert.deepEqual(
    store().list.map((item) => item.id),
    [2],
  );
  // 标已读的回执：回合带上 readAt，未读数以服务端摘要为准
  store().setActive(2);
  store().upsertConversations(summary(2, { unread: 1, turnCount: 2 }));
  store().applyRead(summary(2, { turnCount: 2 }), [
    serverTurn(6, { readAt: '2026-11-25T11:00:00.000Z' }),
  ]);
  assert.equal(store().chats[2].turns[1].readAt, '2026-11-25T11:00:00.000Z');
  assert.equal(store().list[0].unread, 0);
  // 依次打开更多会话：超过上限后最早的那个被淘汰回合，草稿等轻状态保留
  store().setDraft(1, '没发出去的话');
  for (let id = 10; id < 20; id += 1) {
    store().openChat(id);
    store().setTurns(id, [serverTurn(id * 10, { conversationId: id })], 1);
  }
  assert.equal(store().chats[1].loadedFor, null);
  assert.equal(store().chats[1].turns.length, 0);
  assert.equal(store().chats[1].draft, '没发出去的话');
  assert.equal(store().chats[19].turns.length, 1); // 最近打开的还在内存里
  // 发送失败撤下本地回合时，按请求键在所有会话里找
  store().applyLocalTurn(
    {
      ...serverTurn(-5, { requestId: 'local-1', local: true, status: 'pending' }),
      conversationId: 12,
    },
    12,
  );
  store().setActive(1);
  store().removeLocalTurn('local-1');
  assert.equal(
    store().chats[12].turns.some((turn) => turn.requestId === 'local-1'),
    false,
  );
});

test('服务函数：取历史、发消息、已读都带会话；会话增删改走会话接口', async () => {
  const calls = [];
  const api = load('services/agent.ts', {
    '@/services/api': {
      request: async (path, init = {}) => {
        calls.push({ path, init });
        return { conversation: summary(9), turn: serverTurn(1), created: true, success: true };
      },
      getToken: () => 'session',
      absoluteUrl: (path) => path,
      uploadRequest: async () => ({}),
    },
    '@/services/agent-change': {},
    '@/services/normalize-image': {},
  });
  await api.fetchAgentTurns(3, 9, 40);
  assert.equal(
    calls.pop().path,
    '/agent/pets/3/turns?conversationId=9&before=40&protocol=group-v1',
  );
  await api.sendAgentTurn(3, '你好', 'auto', 'req-1', { conversationId: 9, replyTo: 7 });
  assert.deepEqual(JSON.parse(JSON.stringify(calls.pop().init.body)), {
    text: '你好',
    target: 'auto',
    requestId: 'req-1',
    protocol: 'group-v1',
    conversationId: 9,
    replyTo: 7,
  });
  await api.markConversationRead(9);
  assert.equal(calls.pop().path, '/agent/conversations/9/read');
  await api.markConversationRead(9, [7]);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.pop().init.body.ids)), [7]);
  await api.fetchAgentConversations(3, { limit: 20, offset: 20 });
  assert.equal(calls.pop().path, '/agent/pets/3/conversations?limit=20&offset=20');
  await api.createAgentConversation(3);
  assert.equal(calls.pop().init.method, 'POST');
  await api.renameAgentConversation(9, '新名字');
  const renamed = calls.pop();
  assert.equal(renamed.init.method, 'PATCH');
  await api.deleteAgentConversation(9);
  const removed = calls.pop();
  assert.equal(removed.path, '/agent/conversations/9');
  assert.equal(removed.init.method, 'DELETE');
  assert.equal('topicId' in serverTurn(1), false);
});

test('伙伴页：当前会话写在 URL 上，抽屉切换清掉旧的定位参数，主题聚焦已移除', () => {
  const page = read('app/(tabs)/agent.tsx');
  const hook = read('hooks/use-agent-chat.ts');
  const composer = read('components/agent/agent-composer.tsx');
  const message = read('components/agent/agent-message.tsx');
  // 页头用会话列表按钮与当前会话标题；抽屉是伙伴页内的组件，不新增路由
  assert.match(page, /打开会话列表/);
  assert.match(page, /sidebar\.leading/);
  assert.match(page, /<ConversationDrawer/);
  assert.doesNotMatch(page, /setTopic|正在说这件事/);
  assert.match(composer, /说给伙伴听…/);
  assert.doesNotMatch(composer, /inTopic/);
  assert.doesNotMatch(message, /onContinueTopic|继续聊这件事/);
  assert.match(message, /再说一次/); // 失败的回合仍可重发
  // 抽屉里切换：只原地更新参数，不动导航历史；带上会话 id 并清掉上一次深链的定位参数
  assert.match(hook, /router\.setParams\(\{\s*conversationId: String\(id\)/);
  assert.match(hook, /turnId: undefined/);
  assert.doesNotMatch(hook, /topicId: options\.topicId/);
  // 列表为空时自动建一个；本机记住的会话优先
  assert.match(hook, /landingConversation\(state\.list, remembered\.current\)/);
  assert.match(hook, /createAgentConversation\(state\.petId\)/);
  // 抽屉打开时不标已读；伙伴页在前台且抽屉关闭时才标
  assert.match(
    hook,
    /if \(!focused \|\| paused \|\| userId === null \|\| activeUnread === 0\) return;/,
  );
});

test('抽屉与委托簿：同一个 Modal 里承载列表与动作层；回原会话带着会话参数', () => {
  const drawer = read('components/agent/conversation-drawer.tsx');
  const book = read('app/agent-tasks.tsx');
  assert.match(drawer, /<Modal[\s\S]*transparent[\s\S]*animationType="fade"/);
  assert.match(drawer, /accessibilityViewIsModal/);
  assert.match(drawer, /Math\.min\(width \* 0\.82, 360\)/); // 手机 82%，不超过 360
  assert.match(drawer, /useReducedMotion/);
  assert.match(drawer, /重命名/);
  assert.match(drawer, /删除对话/);
  assert.match(drawer, /去委托簿暂停或结束它/); // 删除被拒且委托进行中时的出路
  assert.equal((drawer.match(/<Modal/g) ?? []).length, 1);
  assert.match(book, /conversationId: String\(task\.conversationId\)/);
  assert.match(book, /!task\.conversationDeleted/);
  assert.doesNotMatch(book, /topicId|setTopic/);
});
