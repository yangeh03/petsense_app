const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  parseInline,
  parseAgentMarkdown,
  visibleReplyText,
} = require('../src/components/agent/markdown.ts');
const { scrollRestorePlan } = require('../src/services/chat-restore.ts');
const { careStatusLabel } = require('../src/services/care-labels.ts');

test('single-asterisk pairs become italic without the asterisks (W14 F2)', () => {
  assert.deepEqual(parseInline('结论。*来源：广州市气象台*'), [
    { text: '结论。' },
    { text: '来源：广州市气象台', italic: true },
  ]);
});

test('asterisks next to spaces or other asterisks stay literal', () => {
  assert.deepEqual(parseInline('2 * 3 * 4'), [{ text: '2 * 3 * 4' }]);
  assert.deepEqual(parseInline('重点 * 没有'), [{ text: '重点 * 没有' }]);
  assert.deepEqual(parseInline('**未配对'), [{ text: '**未配对' }]);
});

test('bold still wins over italic, and italic does not pair across lines', () => {
  assert.deepEqual(parseInline('*甲* **乙** *丙*'), [
    { text: '甲', italic: true },
    { text: ' ' },
    { text: '乙', bold: true },
    { text: ' ' },
    { text: '丙', italic: true },
  ]);
  assert.deepEqual(parseInline('*甲\n乙*'), [{ text: '*甲\n乙*' }]);
});

test('asterisk bullets render as list items with a dot marker; other markers stay', () => {
  const blocks = parseAgentMarkdown('* 甲\n* 乙\n- 丙\n2. 丁');
  assert.deepEqual(
    blocks.map((block) => [block.kind, block.marker]),
    [
      ['listItem', '•'],
      ['listItem', '•'],
      ['listItem', '-'],
      ['listItem', '2.'],
    ],
  );
  assert.equal(blocks[0].spans[0].text, '甲');
});

test('a leading speaker mark is removed before rendering; other bracketed words stay (W14 F2)', () => {
  assert.equal(visibleReplyText('【照料助手】主人这句我记下了'), '主人这句我记下了');
  assert.equal(visibleReplyText(' 【爱宠】 \n今天很开心'), '今天很开心');
  assert.equal(visibleReplyText('【注意】这不是发言人'), '【注意】这不是发言人');
  assert.equal(visibleReplyText('【照料助手】'), '');
  assert.equal(visibleReplyText('正文中间的【照料助手】不动'), '正文中间的【照料助手】不动');
});

test('cold start follows the latest message only when there is nothing to restore (W14 F1)', () => {
  assert.deepEqual(scrollRestorePlan(null), { followLatest: true, scrollY: 0 });
  assert.deepEqual(scrollRestorePlan({ scrollY: 320, turns: [] }), {
    followLatest: true,
    scrollY: 320,
  });
  assert.deepEqual(scrollRestorePlan({ scrollY: 320, turns: [{ id: 1 }] }), {
    followLatest: false,
    scrollY: 320,
  });
  assert.deepEqual(scrollRestorePlan({ turns: [{ id: 1 }] }), { followLatest: false, scrollY: 0 });
});

test('care status has a user-facing label; unknown statuses render nothing (W14 K4)', () => {
  assert.equal(careStatusLabel('open'), '提醒中');
  assert.equal(careStatusLabel('ongoing'), '持续关注');
  assert.equal(careStatusLabel('recovered'), '已恢复');
  assert.equal(careStatusLabel('closed'), '已关闭');
  assert.equal(careStatusLabel('something_new'), '');
});

test('换账号时清空会话、草稿、附件及待执行上下文，并标记新账号', () => {
  const { load } = require('./helpers/load-ts.cjs');
  const create = (initialize) => {
    let state;
    const set = (patch) => {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
    };
    state = initialize(set);
    return { getState: () => state };
  };
  const { useAgentChatStore } = load('stores/agent-chat.ts', {
    zustand: { create },
    '@/services/agent-chat-turns': require('../src/services/agent-chat-turns.ts'),
  });
  const before = useAgentChatStore.getState();
  before.resetForUser(1);
  before.setPet({ id: 10, name: '旧账号宠物' });
  before.setList([{ id: 5 }], 1);
  before.setTurns(5, [{ id: 8 }], 1);
  before.setDraft(5, '旧账号草稿');
  before.addAttachments(5, [{ key: 'old-image', status: 'done' }]);
  before.setPending(5, {
    text: '旧操作',
    pet: 10,
    key: 'old-key',
    target: 'auto',
    conversation: 5,
  });
  before.resetForUser(2);
  const after = useAgentChatStore.getState();
  assert.equal(after.ownerId, 2);
  assert.equal(after.listFor, null);
  assert.equal(after.list.length, 0);
  assert.equal(after.activeId, null);
  assert.equal(after.petId, null);
  assert.equal(Object.keys(after.chats).length, 0); // 草稿、附件、待执行上下文都按会话存，随之清空
});

test('乐观回合：本地先上屏、服务端回合按 requestId 对账、分页无更早回合时不报错', () => {
  const { load } = require('./helpers/load-ts.cjs');
  const create = (initialize) => {
    let state;
    const set = (patch) => {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
    };
    state = initialize(set);
    return { getState: () => state };
  };
  const { useAgentChatStore } = load('stores/agent-chat.ts', {
    zustand: { create },
    '@/services/agent-chat-turns': require('../src/services/agent-chat-turns.ts'),
  });
  const store = useAgentChatStore.getState();
  // mock 的 set 整体替换 state 对象，每次都要重新取快照
  store.resetForUser(1);
  store.openChat(5);
  store.setActive(5);
  const ids = () => useAgentChatStore.getState().chats[5].turns.map((turn) => turn.id);
  store.setTurns(5, [{ id: 1 }], 1);
  store.applyLocalTurn(
    {
      id: -1,
      requestId: 'req-1',
      text: '我刚说的话',
      status: 'pending',
      local: true,
    },
    5,
  );
  // 本地乐观回合排在最后
  assert.deepEqual(ids(), [1, -1]);
  // 服务端回合到达后替换本地乐观回合，不重复
  store.applyTurn({
    id: 2,
    requestId: 'req-1',
    text: '我刚说的话',
    status: 'complete',
    conversationId: 5,
  });
  assert.deepEqual(ids(), [1, 2]);
  // 分页返回空（没有更早回合）时列表原样保留，不抛错
  store.prependOlder(5, []);
  assert.deepEqual(ids(), [1, 2]);
  // 没有会话 id 的帧丢弃；别的（没打开的）会话的帧不落到这个会话
  store.applyDelta(null, 3, '不该出现', 'assistant');
  store.applyDelta(6, 3, '别的会话的增量', 'assistant');
  store.applyMessage(6, { id: 'm9', turnId: 3, role: 'assistant', text: '别的会话' });
  assert.deepEqual(ids(), [1, 2]);
});
