const assert = require('node:assert/strict');
const { test } = require('node:test');
const { load } = require('./helpers/load-ts.cjs');
const { Colors } = load('constants/theme.ts', {
  '@/global.css': {},
  'react-native': { Platform: { select: (options) => options.default } },
});
function luminance(hex) {
  const rgb = hex
    .slice(1)
    .match(/../g)
    .map((value) => parseInt(value, 16) / 255);
  const linear = rgb.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + 0.05) / (values[1] + 0.05);
}
test('两种主题的正文、次级文字及主按钮文字达到 4.5:1', () => {
  for (const [scheme, colors] of Object.entries(Colors)) {
    for (const background of ['background', 'backgroundElement', 'backgroundSelected']) {
      for (const foreground of ['text', 'textSecondary']) {
        assert.ok(
          contrast(colors[foreground], colors[background]) >= 4.5,
          `${scheme} ${foreground} on ${background}`,
        );
      }
    }
    assert.ok(contrast(colors.onTint, colors.tint) >= 4.5, `${scheme} primary button`);
    assert.ok(contrast(colors.tintForeground, colors.backgroundElement) >= 4.5, `${scheme} links`);
  }
});

// 品牌色与语义色作「文字」时的口径：压卡片底色至少 4.5:1。
// 实底品牌色（tint/info/online/warning）作图形用，只要求 3:1，属于既有品牌取舍，不在这里卡死。
test('文字专用的品牌色与语义色变体达到 4.5:1', () => {
  for (const [scheme, colors] of Object.entries(Colors)) {
    for (const background of [
      'background',
      'backgroundElement',
      'backgroundSelected',
      'tintBackground',
    ]) {
      for (const foreground of [
        'tintForeground',
        'warningForeground',
        'onlineForeground',
        'infoForeground',
        'danger',
      ]) {
        assert.ok(
          contrast(colors[foreground], colors[background]) >= 4.5,
          `${scheme} ${foreground} on ${background}`,
        );
      }
    }
    // 实底上的文字：徽标数字、按钮文字
    assert.ok(contrast(colors.onDanger, colors.danger) >= 4.5, `${scheme} badge on danger`);
  }
});

// 防止再写死品牌色做文字：tint 作文字在浅色主题只有 2.68:1，必须走 tintForeground。
test('文字不得直接用实底品牌色', () => {
  for (const [scheme, colors] of Object.entries(Colors)) {
    assert.notEqual(colors.tintForeground, undefined, `${scheme} 缺少 tintForeground`);
    if (scheme === 'light') {
      assert.ok(
        contrast(colors.tint, colors.backgroundElement) < 4.5,
        '浅色主题的 tint 本就压不住白底，这条断言用于说明为何文字必须用 tintForeground',
      );
    }
  }
});
