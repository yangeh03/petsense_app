const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  parseAnchorParams,
  findAnchorTurnId,
  needsRemoteAnchor,
  anchorKey,
} = require('../src/services/inbox-anchor.ts');

test('深链参数归一化：字符串/数组/非法值都收敛为 null', () => {
  assert.deepEqual(parseAnchorParams({ turnId: '12', topicId: '3' }), {
    turnId: 12,
    topicId: 3,
    deliveryId: null,
    inboxId: null,
  });
  assert.deepEqual(parseAnchorParams({ turnId: ['8', '9'], inboxId: '4' }), {
    turnId: 8,
    topicId: null,
    deliveryId: null,
    inboxId: 4,
  });
  assert.deepEqual(parseAnchorParams({ turnId: 'abc', topicId: '' }), {
    turnId: null,
    topicId: null,
    deliveryId: null,
    inboxId: null,
  });
});

test('锚点在已加载回合里按 turnId 命中', () => {
  const turns = [{ id: 1 }, { id: 2 }];
  const target = parseAnchorParams({ turnId: '2' });
  assert.equal(findAnchorTurnId(turns, target), 2);
});

test('锚点早于已加载的一页时不在本地命中 → 需要服务端精确取回', () => {
  const turns = [{ id: 90 }, { id: 91 }];
  const target = parseAnchorParams({ turnId: '7' });
  assert.equal(findAnchorTurnId(turns, target), null);
  assert.equal(needsRemoteAnchor(turns, target), true);
});

test('当前话题过滤掉的回信仍能按 deliveryId 命中', () => {
  const turns = [
    { id: 5, blocks: [{ type: 'task_result', task: { deliveryId: 33 } }] },
    { id: 6, blocks: [{ type: 'text' }] },
  ];
  const target = parseAnchorParams({ deliveryId: '33' });
  assert.equal(findAnchorTurnId(turns, target), 5);
  assert.equal(needsRemoteAnchor(turns, target), false);
});

test('安静主题没有交付回合：按 topicId 取权威主题', () => {
  const turns = [{ id: 1, topicId: 9, blocks: [{ type: 'text' }] }];
  const target = parseAnchorParams({ topicId: '9' });
  assert.equal(findAnchorTurnId(turns, target), 1);
  const silent = parseAnchorParams({ topicId: '77' });
  assert.equal(findAnchorTurnId(turns, silent), null);
  assert.equal(needsRemoteAnchor(turns, silent), true);
});

test('锚点身份变化时 key 变化（用于重置已定位状态）', () => {
  const first = parseAnchorParams({ turnId: '1', inboxId: '2' });
  const second = parseAnchorParams({ turnId: '1', inboxId: '3' });
  assert.notEqual(anchorKey(first), anchorKey(second));
});
