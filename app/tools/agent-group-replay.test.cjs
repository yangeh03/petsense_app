/**
 * 用验收夹具录下的真实帧回放客户端的合并规则。
 *
 * fixtures/agent-group-frames.json 由 server/tools/group_fixture_capture.py 对着验收夹具录成：每个剧本的
 * POST 响应、服务端真实推送顺序的帧（领取快照、增量、终态快照、断线重连后的列表）、服务端最终状态。
 * 这里把它们喂给真实的 store（分发与 use-agent-chat.ts 一致），断言最终状态与服务端历史逐条一致、
 * 每条消息的版本与正文不回退。
 *
 * 服务端不会主动制造的到达顺序用同一份录制变体出来：POST 响应迟到（WS 帧先到）、列表响应在途时
 * WS 已经推来更新的增量（列表快照比本地状态旧）。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const zustand = require('zustand');

const { load } = require('./helpers/load-ts.cjs');
const turns = require('../src/services/agent-chat-turns.ts');

const recorded = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'agent-group-frames.json'), 'utf8'),
);
const { useAgentChatStore } = load('stores/agent-chat.ts', {
  zustand,
  '@/services/agent-chat-turns': turns,
});
const PROTOCOL = 'group-v1';
const petId = recorded.cases
  .flatMap((entry) => entry.frames)
  .find((frame) => frame.petId !== undefined).petId;
const caseOf = (name) => recorded.cases.find((entry) => entry.scenario === name);

/** vm 沙箱里的对象原型不同：比较前统一成普通 JSON 对象 */
const json = (value) => JSON.parse(JSON.stringify(value));
const MESSAGE_KEYS = [
  'id',
  'role',
  'position',
  'kind',
  'status',
  'text',
  'version',
  'sources',
  'blocks',
  'error',
  'errorNote',
  'readOnly',
];
const pick = (message) => Object.fromEntries(MESSAGE_KEYS.map((key) => [key, message[key]]));
const store = () => useAgentChatStore.getState();
const turnOf = (id) => store().turns.find((turn) => turn.id === id);

function applyFrame(frame) {
  if (frame.type === 'turn') store().applyTurn(frame.turn);
  else if (frame.type === 'message_delta')
    store().applyMessageDelta(
      frame.petId,
      frame.turnId,
      frame.messageId,
      frame.role,
      frame.text,
      frame.version,
    );
  else if (frame.type === 'message') store().applyMessage(frame.petId, frame.message);
  else if (frame.type === 'message_status')
    store().applyMessageStatus(frame.petId, frame.turnId, frame.messageId, frame.status);
  else if (frame.type === '<refetch>') store().syncTurns(frame.turns);
  // <closed> / <open> 只影响连接状态，不动回合数据
}

function applyPost(frame) {
  store().setDualAgent(frame.body.protocol === PROTOCOL);
  store().applyTurn(frame.body.turn);
}

/** 逐事件检查每条消息的版本、正文、状态不回退；重试让失败的那条重新排队除外 */
function watch(retrying = false) {
  const seen = new Map();
  return () => {
    for (const turn of store().turns) {
      if (!turns.hasMessages(turn)) continue;
      for (const message of turn.messages) {
        const previous = seen.get(message.id);
        seen.set(message.id, message);
        if (!previous) continue;
        const restarted = retrying && previous.status === 'failed' && message.status !== 'failed';
        if (restarted) continue;
        assert.ok(
          message.version >= previous.version,
          `${message.id} 版本回退 ${previous.version} → ${message.version}`,
        );
        assert.ok(
          message.version > previous.version || message.text.length >= previous.text.length,
          `${message.id} 同版本正文回退 ${previous.text.length} → ${message.text.length}`,
        );
        if (previous.status === 'complete')
          assert.equal(message.status, 'complete', `${message.id} 完成后又变回 ${message.status}`);
      }
    }
  };
}

function assertFinalState(turnId, final, label) {
  const turn = turnOf(turnId);
  assert.ok(turn, `${label}：回合不在状态里`);
  assert.ok(!turns.isLocalTurn(turn), `${label}：本地乐观回合没有被服务端回合替换`);
  assert.equal(turn.status, final.status, `${label}：回合状态`);
  assert.equal(turn.target, final.target, `${label}：指向`);
  assert.equal(turn.speaker, final.speaker, `${label}：发言人`);
  assert.deepEqual(
    json(turn.messages.map(pick)),
    json(final.messages.map(pick)),
    `${label}：逐条消息`,
  );
  assert.deepEqual(json(turn.sources), json(final.sources), `${label}：回合来源`);
  assert.deepEqual(json(turn.blocks), json(final.blocks), `${label}：回合卡片`);
}

function replay(entry, { deferPost = false, staleListing = false } = {}) {
  store().resetForUser(1);
  store().setPet({ id: petId, name: '小白' });
  const post = entry.frames.find((frame) => frame.type === '<post>');
  const serverTurn = post.body.turn;
  // 乐观上屏：发送即落一条本地回合，requestId 与请求一致
  store().applyLocalTurn({
    id: turns.nextLocalTurnId(),
    requestId: post.requestId,
    text: serverTurn.text,
    speaker: serverTurn.target === 'pet' ? 'pet' : 'assistant',
    readAt: null,
    target: serverTurn.target,
    replyToMessage: null,
    reply: '',
    status: 'pending',
    createdAt: new Date().toISOString(),
    topicId: null,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
    messages: [],
    local: true,
  });
  const check = watch();
  if (!deferPost) applyPost(post);
  for (const frame of entry.frames) {
    if (frame.type === '<post>') continue;
    applyFrame(frame);
    check();
  }
  if (deferPost) applyPost(post);
  check();
  assertFinalState(entry.turnId, entry.final, entry.scenario);

  if (entry.retry) {
    // use-agent-chat.ts retryTurn：202 的响应是重新进入 pending 的回合本身
    const retryCheck = watch(true);
    const failed = json(turnOf(entry.turnId).messages);
    assert.equal(
      turns.canRetryMessages(turnOf(entry.turnId)),
      true,
      '失败的回合应能只重试失败的那条',
    );
    store().applyTurn(entry.retry.post);
    retryCheck();
    for (const frame of entry.retry.frames) {
      applyFrame(frame);
      retryCheck();
    }
    assertFinalState(entry.turnId, entry.retry.final, `${entry.scenario} 重试后`);
    const retried = turnOf(entry.turnId).messages;
    assert.deepEqual(json(pick(retried[0])), json(pick(failed[0])), '重试动了成功的那条');
    assert.equal(retried[1].id, failed[1].id, '重试后消息 ID 变了');
    assert.ok(retried[1].version > failed[1].version, '重试后版本应递增');
    assert.equal(turns.canRetryMessages(turnOf(entry.turnId)), false, '全部完成后不再有重试入口');
  }

  if (staleListing) {
    // 列表响应在途时 WS 已经推来更新的增量：这份快照里最后一条消息停在第一段增量上
    const final = entry.retry ? entry.retry.final : entry.final;
    const last = final.messages[final.messages.length - 1];
    const firstDelta = [...entry.frames, ...(entry.retry ? entry.retry.frames : [])].find(
      (frame) => frame.type === 'message_delta' && frame.messageId === last.id,
    );
    applyFrame({
      type: '<refetch>',
      turns: [
        {
          ...final,
          status: 'pending',
          messages: final.messages.map((message) =>
            message.id === last.id
              ? {
                  ...message,
                  status: 'running',
                  text: firstDelta.text,
                  version: firstDelta.version,
                  sources: [],
                }
              : message,
          ),
        },
      ],
    });
    check();
    assertFinalState(entry.turnId, final, `${entry.scenario} 旧列表快照之后`);
  }
  return turnOf(entry.turnId);
}

test('录制素材：11 个剧本都收到了终态，旧历史投影成只读消息', () => {
  assert.equal(recorded.cases.length, 11);
  for (const entry of recorded.cases) {
    assert.notEqual(entry.final.status, 'pending', entry.scenario);
    assert.ok(
      entry.frames.some((frame) => frame.type === 'turn' && frame.turn.status !== 'pending'),
    );
  }
  const legacy = recorded.history.turns.find((turn) => turn.id === recorded.history.legacyTurnId);
  assert.equal(legacy.messages.length, 1);
  assert.equal(legacy.messages[0].id, `t${legacy.id}-0`);
  assert.equal(legacy.messages[0].readOnly, true);
});

test('历史：旧回合显示为只读的一条，不提供重试入口，回合级引用取它的正文', () => {
  store().resetForUser(1);
  store().setPet({ id: petId, name: '小白' });
  store().syncTurns(recorded.history.turns);
  const legacyId = recorded.history.legacyTurnId;
  const turn = turnOf(legacyId);
  assert.equal(turn.messages[0].text, '我在。这是一条旧协议的回复。'); // 去掉了发言人标记
  assert.equal(turns.canRetryMessages(turn), false);
  assert.deepEqual(json(turns.quotedReply(store().turns, legacyId, null)), {
    role: 'assistant',
    text: '我在。这是一条旧协议的回复。',
  });
});

for (const entry of recorded.cases) {
  test(`真实帧回放：${entry.scenario}`, () => {
    const orders = [
      replay(entry),
      replay(entry, { deferPost: true }),
      replay(entry, { staleListing: true }),
    ];
    // 到达顺序不同，最终逐条消息完全一致
    for (const other of orders.slice(1))
      assert.deepEqual(
        json(orders[0].messages.map(pick)),
        json(other.messages.map(pick)),
        `${entry.scenario}：到达顺序影响了最终状态`,
      );
    // 每条完成的回复都能被消息级引用精确取到
    for (const message of orders[0].messages)
      assert.deepEqual(json(turns.quotedReply(store().turns, entry.turnId, message.id)), {
        role: message.role,
        text: message.text,
      });
  });
}

test('断线：重连后按列表拉齐，断开前已流出的正文不丢、不重复', () => {
  const entry = caseOf('drop');
  let beforeClose;
  let afterRefetch;
  const turnId = entry.turnId;
  const snapshot = () => json(turnOf(turnId).messages.map((m) => [m.id, m.text]));
  store().resetForUser(1);
  store().setPet({ id: petId, name: '小白' });
  const post = entry.frames.find((frame) => frame.type === '<post>');
  applyPost(post);
  for (const frame of entry.frames) {
    if (frame.type === '<post>') continue;
    if (frame.type === '<closed>') beforeClose = snapshot();
    applyFrame(frame);
    if (frame.type === '<refetch>') afterRefetch = snapshot();
  }
  assert.equal(afterRefetch.length, 2, '重连后应仍是两条气泡');
  assert.equal(beforeClose.length, 2, '断开前已经路由出两条');
  assert.ok(
    afterRefetch[1][1].length >= beforeClose[1][1].length,
    '重连后照料助手已流出的正文回退',
  );
  // 录制必须覆盖「出字途中断开」：断开前照料助手已经有正文（否则要重新录，见 capture 脚本说明）
  assert.notEqual(beforeClose[1][1], '');
  assertFinalState(turnId, entry.final, '断线');
});

test('失败与重试：失败的发言带说明，重试只重跑它、成功的原样保留', () => {
  const entry = caseOf('fail');
  // 只走到失败为止（不点重试），看失败那条在界面数据里的样子
  const turnId = entry.turnId;
  store().resetForUser(1);
  store().setPet({ id: petId, name: '小白' });
  const post = entry.frames.find((frame) => frame.type === '<post>');
  applyPost(post);
  for (const frame of entry.frames) if (frame.type !== '<post>') applyFrame(frame);
  const failed = turnOf(turnId);
  assert.deepEqual(
    failed.messages.map((message) => [
      message.role,
      message.status,
      message.error,
      message.errorNote,
    ]),
    entry.final.messages.map((message) => [
      message.role,
      message.status,
      message.error,
      message.errorNote,
    ]),
  );
  assert.equal(failed.messages[1].status, 'failed');
  assert.equal(failed.messages[1].text, '', '失败的消息不留半截正文');
  assert.equal(turns.canRetryMessages(failed), true);
});

test('联网核实：核实中只挂在生成中的那条消息上，出字后清掉', () => {
  const entry = caseOf('search');
  store().resetForUser(1);
  store().setPet({ id: petId, name: '小白' });
  const post = entry.frames.find((frame) => frame.type === '<post>');
  applyPost(post);
  let sawSearching = false;
  for (const frame of entry.frames) {
    if (frame.type === '<post>') continue;
    applyFrame(frame);
    const message = turnOf(entry.turnId)?.messages.find((item) => item.id === frame.messageId);
    if (frame.type === 'message_status') {
      assert.equal(message.activity, 'searching');
      sawSearching = true;
    } else if (frame.type === 'message_delta') {
      assert.equal(message.activity, undefined);
    }
  }
  assert.ok(sawSearching, '录制里应当有核实中的状态帧');
  assert.equal(turnOf(entry.turnId).messages[0].activity, undefined);
  assert.equal(turnOf(entry.turnId).messages[0].sources[0].type, 'web');
});
