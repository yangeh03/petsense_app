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
