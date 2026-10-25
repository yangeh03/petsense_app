const assert = require('node:assert/strict');
const { test } = require('node:test');
const profile = require('../src/services/pet-profile.ts');
const events = require('../src/services/pet-profile-events.ts');
const pet = {
  id: 1,
  name: '小派',
  species: 'dog',
  breed: '',
  avatar_emoji: '🐶',
  avatarImageUrl: '/uploads/original.png',
};

test('profile names use Unicode code points and reject blank or long breed', () => {
  const draft = profile.createPetDraft(pet);
  assert.equal(profile.validatePetDraft({ ...draft, name: '🐶'.repeat(20) }).name, undefined);
  assert.ok(profile.validatePetDraft({ ...draft, name: '🐶'.repeat(21) }).name);
  assert.ok(profile.validatePetDraft({ ...draft, name: '  ' }).name);
  assert.ok(profile.validatePetDraft({ ...draft, breed: '中'.repeat(41) }).breed);
  assert.ok(profile.validatePetDraft({ ...draft, species: 'bird' }).species);
});
test('editing other fields preserves an existing photo; whitespace alone is not dirty', () => {
  const draft = profile.createPetDraft(pet);
  assert.equal(profile.isPetDraftDirty(pet, { ...draft, name: ' 小派 ' }), false);
  assert.equal(profile.isPetDraftDirty(pet, { ...draft, name: '小团子' }), true);
  assert.deepEqual(profile.buildPetUpdate({ ...draft, name: ' 小团子 ' }), {
    name: '小团子',
    breed: '',
    species: 'dog',
    avatarEmoji: '🐶',
    avatarMode: 'keep',
  });
});
test('photo selection and switching to emoji are explicit mutations', () => {
  const draft = profile.createPetDraft(pet);
  assert.equal(profile.isPetDraftDirty(pet, { ...draft, avatarMode: 'emoji' }), true);
  const photo = { uri: 'file:///pet.png', name: 'pet.png', type: 'image/png' };
  assert.equal(profile.buildPetUpdate({ ...draft, avatarMode: 'photo', photo }).photo, photo);
  assert.ok(profile.validatePetDraft({ ...draft, avatarMode: 'photo', photo: null }).avatar);
  assert.equal(profile.buildPetUpdate({ ...draft, avatarMode: 'emoji', photo }).photo, undefined);
});
test('species labels are localized without guessing missing values', () => {
  assert.equal(profile.speciesLabel('dog'), '狗狗');
  assert.equal(profile.speciesLabel('cat'), '猫咪');
  assert.equal(profile.speciesLabel('rabbit'), '兔子');
  assert.equal(profile.speciesLabel('unknown'), '宠物');
});
test('network save failures have a useful Chinese message while server validation is preserved', () => {
  assert.equal(
    profile.petSaveError(new TypeError('Failed to fetch')),
    '网络连接失败，请检查网络后重试',
  );
  assert.equal(profile.petSaveError(new Error('图片无法读取')), '图片无法读取');
  assert.equal(profile.petSaveError(null), '保存失败，请稍后重试');
});
test('profile invalidation is account scoped and unsubscribe stops updates', () => {
  let mine = 0;
  let other = 0;
  const unsubscribe = events.subscribePetChanges(1, () => {
    mine++;
  });
  const stopOther = events.subscribePetChanges(2, () => {
    other++;
  });
  events.notifyPetChanged(1);
  assert.equal(mine, 1);
  assert.equal(other, 0);
  unsubscribe();
  events.notifyPetChanged(1);
  assert.equal(mine, 1);
  stopOther();
});
