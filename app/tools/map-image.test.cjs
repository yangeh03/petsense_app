const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

const exportsObject = {};
const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/services/map-image.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
vm.runInNewContext(code, { exports: exportsObject, atob });
const { checkMapImage, updateMapImageLoad } = exportsObject;
const png = fs.readFileSync(
  path.join(__dirname, '../../server/petsense/uwb/assets/reconstruction-room-v1/floorplan.png'),
);
const background = {
  width: 714,
  height: 431,
  byteLength: png.length,
  imageDataUri: `data:image/png;base64,${png.toString('base64')}`,
};

test('complete real map passes PNG checks with legacy and checksummed server responses', () => {
  const receipt = checkMapImage(background);
  assert.equal(receipt.byteLength, png.length);
  // Independently computed by Python's zlib.crc32 for this real map asset.
  assert.equal(receipt.crc32, '7d2cc7ad');
  assert.equal(checkMapImage({ ...background, ...receipt }).crc32, receipt.crc32);
});

test('missing chunks, corrupt pixels, wrong dimensions, length or server checksum are rejected', () => {
  const damaged = Buffer.from(png);
  damaged[100] ^= 1;
  for (const bg of [
    { ...background, imageDataUri: background.imageDataUri.slice(0, -20) },
    { ...background, imageDataUri: `data:image/png;base64,${damaged.toString('base64')}` },
    { ...background, width: 715 },
    { ...background, byteLength: png.length - 1 },
    { ...background, crc32: '00000000' },
    { ...background, imageDataUri: 'data:image/png;base64,AAAA' },
    { ...background, width: 10000, height: 10000 },
    undefined,
  ])
    assert.throws(() => checkMapImage(bg), /底图数据不完整或校验失败/);
});

test('image needs both decoder and display confirmation regardless of event order', () => {
  for (const order of [
    ['decoded', 'displayed'],
    ['displayed', 'decoded'],
  ]) {
    const first = updateMapImageLoad(null, 'a', order[0]);
    assert.equal(first.decoded && first.displayed, false);
    const ready = updateMapImageLoad(first, 'a', order[1]);
    assert.equal(ready.decoded && ready.displayed, true);
    assert.equal(ready.error, '');
  }
});

test('late decoder/display callbacks cannot overwrite an error; a different image starts fresh', () => {
  const failed = updateMapImageLoad(null, 'a', 'error', '无法解码');
  assert.equal(updateMapImageLoad(failed, 'a', 'decoded').error, '无法解码');
  assert.equal(updateMapImageLoad(failed, 'a', 'displayed').error, '无法解码');
  const next = updateMapImageLoad(failed, 'b', 'displayed');
  assert.equal(next.error, '');
  assert.equal(next.decoded, false);
});
