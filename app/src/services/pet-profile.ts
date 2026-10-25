import type { SocialPet } from '../types/social';

export const PET_SPECIES = [
  { id: 'dog', label: '狗狗', emoji: '🐶' },
  { id: 'cat', label: '猫咪', emoji: '🐱' },
  { id: 'rabbit', label: '兔子', emoji: '🐰' },
] as const;
export const PET_AVATARS = ['🐶', '🐱', '🐰', '🐾'] as const;
export type PetPhoto = { uri: string; name: string; type: string };
export type PetDraft = {
  name: string;
  species: string;
  breed: string;
  avatarEmoji: string;
  avatarMode: 'keep' | 'photo' | 'emoji';
  photo: PetPhoto | null;
};
export type PetUpdate = Omit<PetDraft, 'photo'> & { photo?: PetPhoto };
export type PetErrors = Partial<Record<'name' | 'breed' | 'species' | 'avatar', string>>;

export function speciesLabel(species: string): string {
  return PET_SPECIES.find((item) => item.id === species)?.label ?? '宠物';
}

export function createPetDraft(pet: SocialPet): PetDraft {
  return {
    name: pet.name,
    species: pet.species,
    breed: pet.breed ?? '',
    avatarEmoji: pet.avatar_emoji,
    avatarMode: 'keep',
    photo: null,
  };
}

export function validatePetDraft(draft: PetDraft): PetErrors {
  const errors: PetErrors = {};
  const nameLength = Array.from(draft.name.trim()).length;
  if (nameLength < 1 || nameLength > 20) errors.name = '请输入 1—20 个字符的名字';
  if (Array.from(draft.breed.trim()).length > 40) errors.breed = '品种最多 40 个字符';
  if (!PET_SPECIES.some((item) => item.id === draft.species)) errors.species = '请选择宠物物种';
  if (draft.avatarMode === 'photo' && !draft.photo) errors.avatar = '请重新选择头像照片';
  return errors;
}

export function buildPetUpdate(draft: PetDraft): PetUpdate {
  return {
    name: draft.name.trim(),
    species: draft.species,
    breed: draft.breed.trim(),
    avatarEmoji: draft.avatarEmoji,
    avatarMode: draft.avatarMode,
    ...(draft.avatarMode === 'photo' && draft.photo ? { photo: draft.photo } : {}),
  };
}

export function isPetDraftDirty(pet: SocialPet, draft: PetDraft): boolean {
  return (
    draft.name.trim() !== pet.name ||
    draft.breed.trim() !== (pet.breed ?? '') ||
    draft.species !== pet.species ||
    draft.avatarEmoji !== pet.avatar_emoji ||
    draft.avatarMode === 'photo' ||
    (draft.avatarMode === 'emoji' && !!pet.avatarImageUrl)
  );
}
