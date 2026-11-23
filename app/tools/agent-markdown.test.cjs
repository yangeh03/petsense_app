const assert = require('node:assert/strict');
const { test } = require('node:test');

const { parseInline, parseAgentMarkdown } = require('../src/components/agent/markdown.ts');

test('paired bold and code spans parse, unpaired markers stay literal', () => {
  assert.deepEqual(parseInline('a **b** `c` ** d'), [
    { text: 'a ' },
    { text: 'b', bold: true },
    { text: ' ' },
    { text: 'c', code: true },
    { text: ' ** d' },
  ]);
});

test('bold does not pair across lines', () => {
  const spans = parseInline('**a\nb**');
  assert.equal(spans.length, 1);
  assert.equal(spans[0].text, '**a\nb**');
});

test('quotes merge consecutive lines into one block, marker stripped', () => {
  const blocks = parseAgentMarkdown('> 第一行\n> 第二行\n正文');
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0].kind, 'quote');
  assert.equal(blocks[0].spans.map((s) => s.text).join(''), '第一行\n第二行');
  assert.equal(blocks[1].kind, 'paragraph');
  assert.equal(blocks[1].spans[0].text, '正文');
});

test('list items keep their original markers', () => {
  const blocks = parseAgentMarkdown('- 甲\n2. 乙\n• 丙');
  assert.deepEqual(
    blocks.map((b) => [b.kind, b.marker]),
    [
      ['listItem', '-'],
      ['listItem', '2.'],
      ['listItem', '•'],
    ],
  );
  assert.equal(blocks[0].spans[0].text, '甲');
});

test('hashtags are not headings and plain text stays a single paragraph', () => {
  const blocks = parseAgentMarkdown('#柴犬日常 #今日份可爱\n下一行');
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].kind, 'paragraph');
  assert.equal(blocks[0].spans[0].text, '#柴犬日常 #今日份可爱\n下一行');
});

test('empty input yields no blocks', () => {
  assert.deepEqual(parseAgentMarkdown(''), []);
});
