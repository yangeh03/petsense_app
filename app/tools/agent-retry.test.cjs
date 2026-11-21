const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

test('retrying a confirmed failed turn restores the original context', () => {
  const source = read('app/(tabs)/agent.tsx');
  // “再说一次”恢复原话、指向和当时的 topicId/replyTo（总会话恢复为 undefined）
  assert.match(source, /setTopic\(turn\.topicId \?\? undefined\)/);
  assert.match(source, /setReplyTo\(turn\.replyTo \?\? undefined\)/);
  // 已确认失败的 pending key 当场失效，避免复用
  assert.match(
    source,
    /pending\.current\?\.key === turn\.requestId[\s\S]{0,80}pending\.current = null/,
  );
});

test('a confirmed failed requestId is never reused when resending', () => {
  const source = read('app/(tabs)/agent.tsx');
  assert.match(source, /failedKeys\.has\(pending\.current\.key\)/);
  assert.match(
    source,
    /turns\.filter\(\(item\) => item\.status === 'failed'\)\.map\(\(item\) => item\.requestId\)/,
  );
});

test('unconfirmed delivery keeps the original key and full parameters', () => {
  const source = read('app/(tabs)/agent.tsx');
  // send 的 catch 只提示错误：原话与 pending 原样保留，可原 key 重试
  assert.match(
    source,
    /} catch \{\s*setError\('暂时没能确认送达，原话已保留，可以重试'\);\s*\} finally \{/,
  );
});
