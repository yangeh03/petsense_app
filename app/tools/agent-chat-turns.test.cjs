const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  applyDeltaToTurns,
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
