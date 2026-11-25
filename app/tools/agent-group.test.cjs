const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const {
  applyMessageDeltaToTurns,
  applyMessageStatusToTurns,
  applyMessageToTurns,
  canRetryMessages,
  hasMessages,
  mergeServerTurns,
  mergeTurn,
  messageIsNewer,
  placeholderRole,
  quotedReply,
  reconcileTurn,
  sendableTarget,
} = require('../src/services/agent-chat-turns.ts');

const { load } = require('./helpers/load-ts.cjs');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

const AT = '2026-11-24T08:00:00.000Z';

function message(id, extra = {}) {
  return {
    id,
    turnId: 7,
    role: 'assistant',
    position: 0,
    kind: 'answer',
    status: 'complete',
    text: '回复正文',
    version: 3,
    sources: [],
    blocks: null,
    error: null,
    errorNote: null,
    readOnly: false,
    createdAt: AT,
    finishedAt: AT,
    ...extra,
  };
}

function turn(id, extra = {}) {
  return {
    id,
    requestId: `req-${id}`,
    text: `主人消息 ${id}`,
    speaker: 'assistant',
    readAt: null,
    target: 'auto',
    reply: '',
    status: 'pending',
    createdAt: AT,
    topicId: null,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
    ...extra,
  };
}

const groupTurn = (id, messages, extra = {}) => turn(id, { messages, ...extra });
const petMessage = (id, extra = {}) => message(id, { role: 'pet', text: '我在呢。', ...extra });
const textOf = (turns, messageId) =>
  turns.find((item) => item.id === 7).messages.find((item) => item.id === messageId).text;

test('消息按版本与正文长度判新旧：版本更高或同版本更长才接受', () => {
  const current = message('m1', { version: 3, text: '今天走了' });
  assert.equal(messageIsNewer(message('m1', { version: 4, text: '' }), current), true);
  assert.equal(
    messageIsNewer(message('m1', { version: 3, text: '今天走了 40 分钟' }), current),
    true,
  );
  // 同版本但更短或相同：乱序的旧帧 / 重复帧
  assert.equal(messageIsNewer(message('m1', { version: 3, text: '今天' }), current), false);
  assert.equal(messageIsNewer(message('m1', { version: 3, text: '今天走了' }), current), false);
  assert.equal(
    messageIsNewer(message('m1', { version: 2, text: '今天走了很久很久' }), current),
    false,
  );
  assert.equal(messageIsNewer(message('m1'), undefined), true);
});

test('乱序与重复的增量不改动已显示的正文（夹具 order 场景的帧序）', () => {
  // 服务端在每段真实增量前后各注入一帧：旧的更短正文（同版本）与重复帧
  const sequence = [
    ['今天', 3],
    ['今天走了 4', 3], // 真实
    ['今天', 3], // 注入的旧帧
    ['今天走了 4', 3], // 注入的重复帧
    ['今天走了 40 分钟。', 4], // 真实（落库后版本推进）
    ['今天走了 4', 3], // 注入的旧帧
  ];
  let turns = [groupTurn(7, [message('m1', { status: 'running', text: '', version: 2 })])];
  for (const [text, version] of sequence) {
    turns = applyMessageDeltaToTurns(turns, 7, 'm1', 'assistant', text, version);
  }
  assert.equal(textOf(turns, 'm1'), '今天走了 40 分钟。');
  assert.equal(turns[0].messages[0].version, 4);
});

test('工具轮之后重新起头的替换带更高版本：更短的正文也被接受', () => {
  let turns = [
    groupTurn(7, [message('m1', { status: 'running', text: '我先查一下记录。', version: 3 })]),
  ];
  turns = applyMessageDeltaToTurns(turns, 7, 'm1', 'assistant', '今天没有', 4);
  assert.equal(textOf(turns, 'm1'), '今天没有');
  turns = applyMessageDeltaToTurns(turns, 7, 'm1', 'assistant', '今天没有走动数据。', 4);
  assert.equal(textOf(turns, 'm1'), '今天没有走动数据。');
});

test('失败的消息由更高版本的空正文快照撤回半截话，并给出只重试这一条的入口', () => {
  let turns = [
    groupTurn(7, [
      petMessage('m1', { status: 'complete', version: 6 }),
      message('m2', { position: 1, status: 'running', text: '我帮你看看记录，', version: 2 }),
    ]),
  ];
  assert.equal(canRetryMessages(turns[0]), false);
  const failed = message('m2', {
    position: 1,
    status: 'failed',
    text: '',
    version: 3,
    error: 'model_error',
    errorNote: '暂时没能接上，稍后再试',
    finishedAt: AT,
  });
  turns = applyMessageToTurns(turns, failed);
  assert.equal(textOf(turns, 'm2'), '');
  assert.equal(
    turns[0].messages.find((item) => item.id === 'm2').errorNote,
    '暂时没能接上，稍后再试',
  );
  assert.equal(turns[0].messages.find((item) => item.id === 'm1').status, 'complete');
  // 回合仍可能在等待收尾帧：入口只在回合结束后出现
  assert.equal(canRetryMessages(turns[0]), false);
  assert.equal(canRetryMessages({ ...turns[0], status: 'complete' }), true);
});

test('重试：失败的消息回到排队、成功的保留，回合状态回到等待被接受', () => {
  const current = groupTurn(
    7,
    [
      petMessage('m1', { status: 'complete', version: 6, finishedAt: AT }),
      message('m2', { position: 1, status: 'failed', text: '', version: 3 }),
    ],
    { status: 'complete' },
  );
  const retried = reconcileTurn(
    [current],
    groupTurn(
      7,
      [
        petMessage('m1', { status: 'complete', version: 6, finishedAt: AT }),
        message('m2', { position: 1, status: 'queued', text: '', version: 4, finishedAt: null }),
      ],
      { status: 'pending' },
    ),
  );
  assert.equal(retried[0].status, 'pending');
  assert.equal(retried[0].messages[1].status, 'queued');
  assert.equal(retried[0].messages[0].text, '我在呢。');
});

test('迟到的轮询快照不把已结束的回合拉回等待，也不盖掉更新的消息', () => {
  const current = groupTurn(
    7,
    [
      petMessage('m1', { status: 'complete', version: 6, text: '我在呢。', finishedAt: AT }),
      message('m2', {
        position: 1,
        status: 'complete',
        text: '今天走了 40 分钟。',
        version: 7,
        finishedAt: AT,
      }),
    ],
    { status: 'complete', readAt: AT },
  );
  const stale = groupTurn(
    7,
    [
      petMessage('m1', { status: 'running', version: 4, text: '我在', finishedAt: null }),
      message('m2', { position: 1, status: 'queued', text: '', version: 1, finishedAt: null }),
    ],
    { status: 'pending', readAt: null },
  );
  const [merged] = [mergeTurn(current, stale)];
  assert.equal(merged.status, 'complete');
  assert.equal(merged.readAt, AT, '回合级已读等字段仍以新快照为准');
  assert.deepEqual(
    merged.messages.map((item) => [item.status, item.text, item.version]),
    [
      ['complete', '我在呢。', 6],
      ['complete', '今天走了 40 分钟。', 7],
    ],
  );
});

test('POST 响应晚于 WS 帧：同一回合按消息合并，不回退', () => {
  const viaWs = groupTurn(7, [
    message('m1', { status: 'running', text: '今天走了 40', version: 5 }),
  ]);
  const viaPost = groupTurn(7, [message('m1', { status: 'queued', text: '', version: 1 })]);
  const [merged] = [mergeTurn(viaWs, viaPost)];
  assert.equal(textOf([merged], 'm1'), '今天走了 40');
  assert.equal(merged.messages[0].status, 'running');
});

test('快照没到时增量先建占位，快照到达后补齐归属与位置', () => {
  let turns = [turn(6, { status: 'complete' })];
  // 重连后拉齐之前，WS 增量先到：回合未知
  turns = applyMessageDeltaToTurns(turns, 7, 'm9', 'assistant', '今天走了', 2);
  assert.equal(turns.length, 2);
  assert.equal(turns[1].id, 7);
  assert.equal(turns[1].messages[0].position, Number.MAX_SAFE_INTEGER);
  // 拉齐的列表到达：补齐主人的话、消息位置与发言顺序
  const listed = groupTurn(7, [
    petMessage('m8', { position: 0, status: 'running', text: '我在呢', version: 2 }),
    message('m9', { position: 1, status: 'running', text: '今天', version: 2 }),
  ]);
  turns = mergeServerTurns(turns, [listed]);
  assert.equal(turns[1].text, '主人消息 7');
  assert.deepEqual(
    turns[1].messages.map((item) => [item.id, item.position, item.text]),
    [
      ['m8', 0, '我在呢'],
      ['m9', 1, '今天走了'],
    ],
  );
  assert.equal(turns[1].messages[1].partial, undefined);
});

test('消息快照并入所属回合；回合未知时建占位回合', () => {
  let turns = [groupTurn(7, [message('m1', { status: 'running', text: '今天', version: 2 })])];
  turns = applyMessageToTurns(
    turns,
    message('m1', { status: 'complete', text: '今天走了 40 分钟。', version: 4, finishedAt: AT }),
  );
  assert.equal(turns[0].messages[0].text, '今天走了 40 分钟。');
  assert.equal(turns[0].messages[0].status, 'complete');
  // 旧帧（同 id、更早的状态）不回退
  const count = turns[0].messages[0].version;
  turns = applyMessageToTurns(
    turns,
    message('m1', { status: 'running', text: '今天走了', version: 3, finishedAt: null }),
  );
  assert.equal(turns[0].messages[0].version, count);
  turns = applyMessageToTurns(turns, message('m5', { turnId: 9 }));
  assert.equal(turns[1].id, 9);
  assert.equal(turns[1].messages[0].id, 'm5');
});

test('联网核实的瞬时进度只挂在生成中的消息上，出字后消失', () => {
  let turns = [groupTurn(7, [message('m1', { status: 'running', text: '', version: 2 })])];
  turns = applyMessageStatusToTurns(turns, 7, 'm1', 'searching');
  assert.equal(turns[0].messages[0].activity, 'searching');
  // 未知消息或已结束的消息不挂
  assert.equal(applyMessageStatusToTurns(turns, 7, 'm2', 'searching'), turns);
  // 新正文到了：不再显示核实中
  turns = applyMessageDeltaToTurns(turns, 7, 'm1', 'assistant', '查到了', 2);
  assert.equal(turns[0].messages[0].activity, undefined);
  assert.equal(textOf(turns, 'm1'), '查到了');
});

test('拉齐列表时已加载的回合逐条合并，旧协议与本地乐观回合照旧', () => {
  const local = turn(-1, { requestId: 'req-local', local: true });
  const existing = [
    turn(1, { status: 'complete' }),
    groupTurn(2, [message('m1', { status: 'running', text: '今天走了 40', version: 5 })]),
    local,
  ];
  const merged = mergeServerTurns(existing, [
    groupTurn(2, [message('m1', { status: 'running', text: '今天', version: 2 })]),
  ]);
  assert.deepEqual(
    merged.map((item) => item.id),
    [1, 2, -1],
  );
  assert.equal(merged[1].messages[0].text, '今天走了 40');
});

test('旧协议的回合没有 messages：新旧形状互切时以服务端为准', () => {
  assert.equal(hasMessages(turn(1)), false);
  assert.equal(hasMessages(groupTurn(1, [])), true);
  const legacy = turn(1, { status: 'complete', reply: '旧协议回复' });
  const grouped = groupTurn(1, [message('m1')], { status: 'complete' });
  assert.deepEqual(mergeTurn(legacy, grouped), grouped);
  assert.deepEqual(mergeTurn(grouped, legacy), legacy);
  // 还没路由的回合（messages 为空）不会把已有消息清掉
  const [merged] = [mergeTurn(grouped, groupTurn(1, []))];
  assert.equal(merged.messages.length, 1);
});

test('旧回合的投影消息没有版本：卡片状态变化以服务端快照为准', () => {
  const projected = (status) =>
    message('t3-0', {
      readOnly: true,
      version: 1,
      status,
      blocks: [{ type: 'proposal', proposal: { id: 11, kind: 'profile_update', status } }],
    });
  const [merged] = [
    mergeTurn(
      groupTurn(3, [projected('pending')], { status: 'complete' }),
      groupTurn(3, [projected('superseded')], { status: 'complete' }),
    ),
  ];
  assert.equal(merged.messages[0].blocks[0].proposal.status, 'superseded');
});

test('「两位都回」只在开放双 Agent 时可发；等待占位按发言对象归属', () => {
  assert.equal(sendableTarget('both', true), 'both');
  assert.equal(sendableTarget('both', false), 'auto');
  assert.equal(sendableTarget('pet', false), 'pet');
  // W18：派发是确定的——只点名一位就是那位；未指定与两位都回都由爱宠先接
  assert.equal(placeholderRole('pet'), 'pet');
  assert.equal(placeholderRole('assistant'), 'assistant');
  assert.equal(placeholderRole('both'), 'pet');
  assert.equal(placeholderRole('auto'), 'pet');
  // 带引用时先发言的是被引用的那位，不预设发言人
  assert.equal(placeholderRole('pet', true), 'pet');
  assert.equal(placeholderRole('assistant', true), 'assistant');
  assert.equal(placeholderRole('both', true), null);
  assert.equal(placeholderRole('auto', true), null);
});

test('引用解析：消息级精确到那一条，回合级取最后一条有正文的话', () => {
  const source = groupTurn(5, [
    message('m1', { role: 'pet', text: '【爱宠】今天一起晒了太阳。', status: 'complete' }),
    message('m2', { role: 'assistant', text: '', status: 'failed' }),
    message('m3', { role: 'assistant', text: '它今天走了 42 分钟。', status: 'complete' }),
  ]);
  const turns = [source, { ...groupTurn(6, []), replyTo: 5 }];
  assert.deepEqual(JSON.parse(JSON.stringify(quotedReply(turns, 5, 'm1'))), {
    role: 'pet',
    text: '今天一起晒了太阳。',
  });
  assert.equal(quotedReply(turns, 5, 'm3').text, '它今天走了 42 分钟。');
  assert.equal(quotedReply(turns, 5, 'm2'), null); // 失败的那条没有正文
  // 回合级引用：跳过没有正文的那条，取最后一条有正文的
  assert.deepEqual(JSON.parse(JSON.stringify(quotedReply(turns, 5))), {
    role: 'assistant',
    text: '它今天走了 42 分钟。',
  });
  assert.equal(quotedReply(turns, 6), null); // 引用的回合没有正文
  assert.equal(quotedReply(turns, 99), null); // 不在已加载范围
  assert.equal(quotedReply(turns, null), null);
  // 旧协议的回合没有逐条回复：按回合的发言人与正文回退，解析不了消息级引用
  const legacy = [
    { ...turns[1], messages: undefined, speaker: 'assistant', reply: '【照料助手】在看。' },
  ];
  assert.deepEqual(JSON.parse(JSON.stringify(quotedReply(legacy, 6))), {
    role: 'assistant',
    text: '在看。',
  });
  assert.equal(quotedReply(legacy, 6, 'm1'), null);
});

test('服务函数在请求上声明群聊协议；引用精确到消息', async () => {
  const calls = [];
  const services = load('services/agent.ts', {
    '@/services/api': {
      request: async (path, init) => {
        calls.push({ path, init });
        return { turn: { id: 1 }, protocol: 'group-v1' };
      },
      getToken: () => 'session',
      absoluteUrl: (path) => path,
      uploadRequest: async () => ({}),
    },
  });
  assert.equal(services.AGENT_GROUP_PROTOCOL, 'group-v1');
  const result = await services.sendAgentTurn(3, '今天走了多久', 'both', 'req-1', {
    conversationId: 9,
    replyTo: 9,
    replyToMessage: 'm12',
    images: ['img-1'],
  });
  assert.equal(result.protocol, 'group-v1');
  assert.equal(calls[0].path, '/agent/pets/3/turns');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].init.body)), {
    text: '今天走了多久',
    target: 'both',
    requestId: 'req-1',
    protocol: 'group-v1',
    conversationId: 9,
    replyTo: 9,
    replyToMessage: 'm12',
    images: ['img-1'],
  });
  // 没有消息级引用时不带这个字段（旧协议的服务端不看它）
  await services.sendAgentTurn(3, '你好', 'auto', 'req-2', { conversationId: 9 });
  assert.equal('replyToMessage' in calls[1].init.body, false);
  // 历史列表与已读回执同样声明协议：服务端据此返回逐条回复
  await services.fetchAgentTurns(3, 9, 20);
  assert.match(
    calls[2].path,
    /\/agent\/pets\/3\/turns\?conversationId=9&before=20&protocol=group-v1$/,
  );
  await services.markConversationRead(9, [7]);
  assert.equal(calls[3].path, '/agent/conversations/9/read');
  assert.equal(calls[3].init.body.protocol, 'group-v1');
  await services.retryAgentTurn(3, 7);
  assert.equal(calls[4].path, '/agent/pets/3/turns/7/retry');
  assert.equal(calls[4].init.method, 'POST');
});

test('store：WS 帧落到对应会话的回合消息上；引用按会话各自保存', () => {
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
  store.resetForUser(1);
  store.setPet({ id: 3, name: '豆豆' });
  store.setDualAgent(true);
  assert.equal(useAgentChatStore.getState().dualAgent, true);
  store.openChat(5);
  store.setActive(5);
  store.setTurns(5, [groupTurn(7, [message('m1', { status: 'queued', text: '', version: 1 })])], 1);
  store.applyMessage(5, message('m1', { status: 'running', text: '', version: 2 }));
  store.applyMessageDelta(5, 7, 'm1', 'assistant', '今天走了', 2);
  const turns = () => useAgentChatStore.getState().chats[5].turns;
  assert.equal(textOf(turns(), 'm1'), '今天走了');
  // 没有会话 id 的帧丢弃；别的（没打开的）会话的帧不落到这个会话
  store.applyMessageDelta(null, 7, 'm1', 'assistant', '没有会话的帧', 9);
  store.applyMessageDelta(6, 7, 'm1', 'assistant', '别的会话的帧', 9);
  store.applyMessageStatus(6, 7, 'm1', 'searching');
  assert.equal(textOf(turns(), 'm1'), '今天走了');
  store.applyMessageStatus(5, 7, 'm1', 'searching');
  assert.equal(turns()[0].messages[0].activity, 'searching');
  store.applyMessageDelta(5, 7, 'm1', 'assistant', '今天走了 42 分钟', 2);
  // 新正文到了，联网核实之类的瞬时进度不再显示
  assert.equal(turns()[0].messages[0].activity, undefined);
  assert.equal(textOf(turns(), 'm1'), '今天走了 42 分钟');
  // 引用按会话保存：切到别的会话后原会话的引用还在，新会话不受影响
  store.setReplyTo(5, 7);
  store.setReplyToMessage(5, 'm1');
  store.openChat(6);
  store.setActive(6);
  let current = useAgentChatStore.getState();
  assert.equal(current.chats[5].replyTo, 7);
  assert.equal(current.chats[5].replyToMessage, 'm1');
  assert.equal(current.chats[6].replyTo, undefined);
  assert.equal(current.chats[6].replyToMessage, undefined);
  store.resetForUser();
  current = useAgentChatStore.getState();
  assert.equal(current.dualAgent, false);
  assert.equal(Object.keys(current.chats).length, 0);
});

test('群聊渲染：逐条气泡、角色状态与只重试失败的那条都在消息组件里', () => {
  const component = read('components/agent/agent-message.tsx');
  const composer = read('components/agent/agent-composer.tsx');
  const channel = read('services/agent-channel.ts');
  const page = read('app/(tabs)/agent.tsx');
  // 连接声明协议；按消息 ID 的三种帧都接
  assert.match(channel, /protocol=\$\{AGENT_GROUP_PROTOCOL\}/);
  assert.match(channel, /type: 'message_delta'/);
  assert.match(channel, /type: 'message_status'/);
  // 逐条渲染与等待文案
  assert.match(component, /hasMessages\(turn\)/);
  assert.match(component, /准备接话…/);
  assert.match(component, /正在联网核实…/);
  assert.match(component, /再试一次/);
  assert.match(component, /onReplyTo/);
  // 输入区的「两位都回」与引用预览
  assert.match(composer, /两位都回/);
  assert.match(composer, /onCancelQuote/);
  // 页面：失败时优先只重试失败的发言，旧回合仍回填原话
  assert.match(page, /canRetryMessages\(turn\)/);
  assert.match(page, /retryTurn/);
});
