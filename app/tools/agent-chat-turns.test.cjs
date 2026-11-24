const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  applyDeltaToTurns,
  dedupeTurns,
  compareTurns,
  isLocalTurn,
  mergeServerTurns,
  nextLocalTurnId,
  orderTurns,
  reconcileTurn,
  removeLocalTurn,
  upsertLocalTurn,
} = require('../src/services/agent-chat-turns.ts');

function serverTurn(id, extra = {}) {
  return {
    id,
    requestId: `req-${id}`,
    text: `消息 ${id}`,
    speaker: 'assistant',
    readAt: null,
    target: 'auto',
    reply: `回复 ${id}`,
    status: 'complete',
    createdAt: '2026-11-24T00:00:00.000Z',
    topicId: null,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
    ...extra,
  };
}

function localTurn(requestId, extra = {}) {
  return {
    id: nextLocalTurnId(),
    requestId,
    text: '我刚说的话',
    speaker: 'assistant',
    readAt: null,
    target: 'auto',
    reply: '',
    status: 'pending',
    createdAt: '2026-11-24T00:00:00.000Z',
    topicId: null,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
    local: true,
    ...extra,
  };
}

test('本地乐观回合排在服务端回合之后，服务端回合按 id 正序', () => {
  const local = localTurn('req-local');
  const turns = orderTurns([serverTurn(3), local, serverTurn(1), serverTurn(2)]);
  assert.deepEqual(
    turns.map((turn) => turn.id),
    [1, 2, 3, local.id],
  );
  assert.equal(isLocalTurn(turns[3]), true);
  assert.equal(isLocalTurn(turns[0]), false);
});

test('服务端回合到达时替换同 requestId 的本地乐观回合（不重复、不残留）', () => {
  const local = localTurn('req-local');
  const turns = reconcileTurn([serverTurn(1), local], serverTurn(2, { requestId: 'req-local' }));
  assert.equal(turns.length, 2);
  assert.equal(turns.filter((turn) => isLocalTurn(turn)).length, 0);
  assert.equal(turns[1].id, 2);
  assert.equal(turns[1].text, '消息 2');
});

test('服务端回合按 id 覆盖同 id 的旧快照', () => {
  const turns = reconcileTurn([serverTurn(1, { reply: '旧' })], serverTurn(1, { reply: '新' }));
  assert.equal(turns.length, 1);
  assert.equal(turns[0].reply, '新');
});

test('本地乐观回合同 requestId 重复插入只保留一条', () => {
  const local = localTurn('req-local');
  const turns = upsertLocalTurn([local], localTurn('req-local'));
  assert.equal(turns.length, 1);
  assert.equal(turns[0].requestId, 'req-local');
});

test('撤下本地乐观回合不影响服务端回合', () => {
  const local = localTurn('req-local');
  const turns = removeLocalTurn([serverTurn(1), local], 'req-local');
  assert.deepEqual(
    turns.map((turn) => turn.id),
    [1],
  );
});

test('流式增量归到本地乐观回合上，不再另建占位气泡', () => {
  const local = localTurn('req-local');
  const turns = applyDeltaToTurns([local], 42, '它在回应', 'assistant');
  assert.equal(turns.length, 1);
  assert.equal(turns[0].id, local.id);
  assert.equal(turns[0].reply, '它在回应');
  assert.equal(turns[0].speaker, 'assistant');
});

test('没有本地回合时，流式增量仍建占位回合', () => {
  const turns = applyDeltaToTurns([serverTurn(1)], 42, '片段', 'assistant');
  assert.equal(turns.length, 2);
  assert.equal(turns[1].id, 42);
  assert.equal(turns[1].text, '');
  assert.equal(turns[1].reply, '片段');
  assert.equal(turns[1].status, 'pending');
});

test('终态回合忽略后来的流式增量（以服务端已保存结果为准）', () => {
  const turns = applyDeltaToTurns([serverTurn(1)], 1, '迟到的片段', 'assistant');
  assert.equal(turns[0].reply, '回复 1');
});

test('拉齐服务端最新回合时保留本地乐观回合并排在最后', () => {
  const local = localTurn('req-local');
  const turns = mergeServerTurns([serverTurn(1), local], [serverTurn(2), serverTurn(3)]);
  assert.deepEqual(
    turns.map((turn) => turn.id),
    [1, 2, 3, local.id],
  );
});

test('服务端还没有回合时，本地乐观回合仍在', () => {
  const local = localTurn('req-local');
  const turns = mergeServerTurns([serverTurn(1), local], []);
  assert.deepEqual(
    turns.map((turn) => turn.id),
    [local.id],
  );
});

test('本地回合 id 为负且互不相同', () => {
  const a = nextLocalTurnId();
  const b = nextLocalTurnId();
  assert.ok(a < 0 && b < 0);
  assert.notEqual(a, b);
  // 更新的本地回合 id 更负，排序时排在后面（更靠下）
  assert.ok(compareTurns(localTurn('x', { id: a }), localTurn('y', { id: b })) < 0);
});

// --- 渲染键唯一性（2026-11-24 视觉走查 D-01）---
// 列表渲染键取 requestId || id；同键条目并存会让 React 丢弃或复用错位的气泡。

test('去重：同一 requestId 的两条回合收敛成一条，保留服务端那条', () => {
  const turns = dedupeTurns([
    serverTurn(44, { requestId: 'care:review:daily:1:2026-11-23', reply: '服务端正文' }),
    serverTurn(45, { requestId: 'care:review:daily:1:2026-11-23', reply: '另一条' }),
  ]);
  assert.equal(turns.length, 1);
  assert.equal(turns[0].id, 45);
});

test('去重：本地乐观回合与服务端同 requestId 时保留服务端那条', () => {
  const local = localTurn('req-1');
  const turns = dedupeTurns([local, serverTurn(9, { requestId: 'req-1' })]);
  assert.equal(turns.length, 1);
  assert.equal(turns[0].id, 9);
  assert.ok(!isLocalTurn(turns[0]));
});

test('去重：requestId 不同但 id 相同也收敛成一条', () => {
  const turns = dedupeTurns([serverTurn(7, { requestId: 'a' }), serverTurn(7, { requestId: 'b' })]);
  assert.equal(turns.length, 1);
});

test('去重后渲染键（requestId || id）全局唯一', () => {
  const turns = dedupeTurns([
    serverTurn(44, { requestId: 'care:review:daily:1:2026-11-23' }),
    serverTurn(45, { requestId: 'care:review:daily:1:2026-11-23' }),
    serverTurn(46, { requestId: 'req-46' }),
    localTurn('care:review:daily:1:2026-11-23'),
  ]);
  const keys = turns.map((turn) => turn.requestId || turn.id);
  assert.equal(new Set(keys).size, keys.length);
});

test('拉齐服务端回合时脏快照带来的同 requestId 重复不会进列表', () => {
  const dirty = serverTurn(44, { requestId: 'care:review:daily:1:2026-11-23' });
  const turns = mergeServerTurns([dirty, dirty], [serverTurn(45, { requestId: 'req-45' })]);
  const keys = turns.map((turn) => turn.requestId || turn.id);
  assert.equal(new Set(keys).size, keys.length);
});
