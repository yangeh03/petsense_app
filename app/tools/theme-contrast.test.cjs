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
