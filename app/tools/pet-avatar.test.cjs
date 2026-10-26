const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { module, exports: module.exports, require: (name) => dependencies[name] },
  );
  return module.exports;
}
const selector = () => load('../src/services/pet-avatar.ts');
const pet = { device_id: 'collar', petgif_id: 'my-pet', avatarImageUrl: '/avatar.png' };
const petgif = {
  id: 'my-pet',
  characterCardUrl: '/character.png',
  behaviors: {
    stand: { gifUrl: '/stand.gif' },
    sit: { gifUrl: '/sit.gif' },
    lie_down: { gifUrl: '/lie.gif' },
    walk: { gifUrl: '/walk.gif' },
  },
};
const frame = (label, confidence = 1) => ({
  deviceId: 'collar',
  health: { valid: false },
  edge: { action: { label, confidence } },
});
function select(extra = {}) {
  return selector().selectPetAvatar({
    pet: { ...pet, avatar_template_id: 'marni' },
    petgif,
    frame: frame('walking'),
    fresh: true,
    ...extra,
  });
}
test('each supported edge action selects only its own preset even with invalid health', () => {
  for (const [label, key, url] of [
    ['standing', 'stand', '/stand.gif'],
    ['sitting', 'sit', '/sit.gif'],
    ['lying down', 'lie_down', '/lie.gif'],
    ['walking', 'walk', '/walk.gif'],
  ]) {
    const result = select({ frame: frame(label) });
    assert.equal(result.actionKey, key);
    assert.equal(result.animationUrl, url);
  }
});
test('unknown, warming up and uncertain edge predictions never borrow cloud or time guesses', () => {
  for (const input of [
    frame('unknown'),
    frame('warming_up', 0),
    frame('walking', 0.59),
    frame('walking', NaN),
  ]) {
    input.action = { actionKey: 'run', confidence: 1 };
    const result = select({ frame: input });
    assert.equal(result.actionKey, null);
    assert.equal(result.animationUrl, null);
  }
});
test('stale, unbound and unrelated frames stop animation and preserve the profile avatar', () => {
  for (const extra of [
    { fresh: false },
    { frame: null },
    { pet: { ...pet, device_id: '' } },
    { frame: { ...frame('walking'), deviceId: 'someone-else' } },
  ]) {
    const result = select(extra);
    assert.equal(result.animationUrl, null);
    assert.equal(result.staticUrl, '/avatar.png');
  }
});
test('missing preset never plays a different action; another pet animation is ignored', () => {
  const missing = select({ petgif: { ...petgif, behaviors: { sit: petgif.behaviors.sit } } });
  assert.equal(missing.actionKey, 'walk');
  assert.equal(missing.animationUrl, null);
  assert.equal(select({ petgif: { ...petgif, id: 'other-pet' } }).animationUrl, null);
});
test('profile image falls back to own character card, never another pet card', () => {
  assert.equal(select({ pet: { ...pet, avatarImageUrl: null } }).staticUrl, '/character.png');
  assert.equal(
    select({ pet: { ...pet, avatarImageUrl: null }, petgif: { ...petgif, id: 'other' } }).staticUrl,
    null,
  );
});
test('a selected template supplies exact action GIF after personal behavior is missing', () => {
  const template = {
    id: 'marni',
    characterCardUrl: '/template.png',
    behaviors: { walk: { gifUrl: '/template-walk.gif' }, sit: { gifUrl: '/template-sit.gif' } },
  };
  const result = selector().selectPetAvatar({
    pet: { ...pet, avatar_template_id: 'marni' },
    petgif: { ...petgif, behaviors: { sit: petgif.behaviors.sit } },
    avatarTemplate: template,
    frame: frame('walking'),
    fresh: true,
  });
  assert.equal(result.animationUrl, '/template-walk.gif');
  assert.equal(result.staticUrl, '/avatar.png');
});
test('template card is the still-image fallback after personal identity and user photo', () => {
  const result = selector().selectPetAvatar({
    pet: { ...pet, petgif_id: null, avatar_template_id: 'marni', avatarImageUrl: null },
    petgif: null,
    avatarTemplate: { id: 'marni', characterCardUrl: '/template.png', behaviors: {} },
    frame: null,
    fresh: false,
  });
  assert.equal(result.animationUrl, null);
  assert.equal(result.staticUrl, '/template.png');
});
test('a selected template replaces the profile photo with a standing preset while offline', () => {
  const result = selector().selectPetAvatar({
    pet: { ...pet, avatar_template_id: 'marni' },
    petgif,
    avatarTemplate: { id: 'marni', characterCardUrl: '/template.png', behaviors: {} },
    frame: null,
    fresh: false,
  });
  assert.equal(result.animationUrl, null);
  assert.equal(result.staticUrl, '/template.png');
  assert.equal(result.label, '静止站立');
});
test('an offline template preview plays its selected action, but live telemetry wins', () => {
  const avatarTemplate = {
    id: 'marni',
    characterCardUrl: '/template.png',
    behaviors: { walk: { gifUrl: '/template-walk.gif' }, sit: { gifUrl: '/template-sit.gif' } },
  };
  const preview = selector().selectPetAvatar({
    pet: { ...pet, petgif_id: null, avatar_template_id: 'marni' },
    petgif: null,
    avatarTemplate,
    frame: null,
    fresh: false,
    previewAction: 'walk',
  });
  assert.equal(preview.animationUrl, '/template-walk.gif');
  assert.equal(preview.label, '走动');

  const live = selector().selectPetAvatar({
    pet: { ...pet, petgif_id: null, avatar_template_id: 'marni' },
    petgif: null,
    avatarTemplate,
    frame: frame('sitting'),
    fresh: true,
    previewAction: 'walk',
  });
  assert.equal(live.animationUrl, '/template-sit.gif');
  assert.equal(live.label, '坐着');
});
test('a late image load cannot replace a newer requested pet action', () => {
  const { shouldPromotePetMedia } = selector();
  assert.equal(shouldPromotePetMedia?.('/template-sit.gif', '/template-sit.gif'), true);
  assert.equal(shouldPromotePetMedia?.('/template-walk.gif', '/template-sit.gif'), false);
});
test('legacy hook follows reported predictions and never invents movement or sleep', () => {
  const { inferPetAction } = selector();
  assert.equal(inferPetAction(null), null);
  assert.equal(inferPetAction({ imu: { ok: true, accel: [3, 2, 1] } }), null);
  assert.equal(inferPetAction({ action: { actionKey: 'sit', confidence: 0.8 } }), 'sit');
  assert.equal(inferPetAction({ action: { actionKey: 'sleep', confidence: 0.2 } }), null);
});
test('failed images step through action GIF, profile, character card and emoji; clearing failures retries GIF', () => {
  const missingGif = select({ failedUrls: ['/walk.gif'] });
  assert.equal(missingGif.animationUrl, null);
  assert.equal(missingGif.staticUrl, '/avatar.png');
  const missingProfile = select({ failedUrls: ['/walk.gif', '/avatar.png'] });
  assert.equal(missingProfile.staticUrl, '/character.png');
  assert.equal(
    select({ failedUrls: ['/walk.gif', '/avatar.png', '/character.png'] }).staticUrl,
    null,
  );
  assert.equal(select({ failedUrls: [] }).animationUrl, '/walk.gif');
});
