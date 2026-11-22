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
  avatarTemplateId: string | null;
  photo: PetPhoto | null;
  /** W03 生日（D1 精度感知）：'' = 未填写/清空；支持 2023 / 2023-09 / 2023-09-10 */
  birthDate: string;
};
export type PetUpdate = Omit<PetDraft, 'photo' | 'birthDate'> & {
  photo?: PetPhoto;
  birthDate: string | null; // null = 清空生日
};
export type PetErrors = Partial<
  Record<'name' | 'breed' | 'species' | 'avatar' | 'birthDate', string>
>;

const BIRTH_INPUT_RE = /^\d{4}(-\d{2})?(-\d{2})?$/;

export function isValidBirthInput(value: string): boolean {
  if (!BIRTH_INPUT_RE.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  if (month != null && (month < 1 || month > 12)) return false;
  if (day != null && (day < 1 || day > 31)) return false;
  const now = new Date();
  return year >= 1990 && year <= now.getFullYear();
}

type BirthLike = { birthDate?: string | null; birthDatePrecision?: string | null };

/** 生日展示（精度感知）：2023-09-10 / 约 2023 年 9 月 / 约 2023 年。 */
export function birthLabel(pet: BirthLike): string {
  if (!pet.birthDate) return '未填写';
  const [year, month, day] = pet.birthDate.split('-');
  if (pet.birthDatePrecision === 'year') return `约 ${year} 年`;
  if (pet.birthDatePrecision === 'month') return `约 ${year} 年 ${Number(month)} 月`;
  return `${year}-${month}-${day ?? '01'}`;
}

/** 年龄展示：按精度标「约」（D1）。档案未填生日时返回 null。 */
export function ageLabel(pet: BirthLike): string | null {
  if (!pet.birthDate) return null;
  const [year, month, day] = pet.birthDate.split('-').map(Number);
  const now = new Date();
  const birth = new Date(Date.UTC(year, (month ?? 1) - 1, day ?? 1));
  let age = now.getFullYear() - birth.getFullYear();
  const beforeBirthday =
    now.getMonth() < birth.getMonth() ||
    (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate());
  if (beforeBirthday) age -= 1;
  if (age < 0) return null;
  const approximate = pet.birthDatePrecision !== 'day';
  return `${approximate ? '约 ' : ''}${age} 岁`;
}

export function speciesLabel(species: string): string {
  return PET_SPECIES.find((item) => item.id === species)?.label ?? '宠物';
}

export function petSaveError(error: unknown): string {
  if (error instanceof TypeError) return '网络连接失败，请检查网络后重试';
  return error instanceof Error ? error.message : '保存失败，请稍后重试';
}

export function createPetDraft(pet: SocialPet): PetDraft {
  return {
    name: pet.name,
    species: pet.species,
    breed: pet.breed ?? '',
    avatarEmoji: pet.avatar_emoji,
    avatarMode: 'keep',
    avatarTemplateId: pet.avatar_template_id ?? null,
    photo: null,
    birthDate: pet.birthDate ?? '',
  };
}

export function validatePetDraft(draft: PetDraft): PetErrors {
  const errors: PetErrors = {};
  const nameLength = Array.from(draft.name.trim()).length;
  if (nameLength < 1 || nameLength > 20) errors.name = '请输入 1—20 个字符的名字';
  if (Array.from(draft.breed.trim()).length > 40) errors.breed = '品种最多 40 个字符';
  if (!PET_SPECIES.some((item) => item.id === draft.species)) errors.species = '请选择宠物物种';
  if (draft.avatarMode === 'photo' && !draft.photo) errors.avatar = '请重新选择头像照片';
  const birth = draft.birthDate.trim();
  if (birth && !isValidBirthInput(birth)) {
    errors.birthDate = '生日格式：2023-09-10 / 2023-09 / 2023，年份需在 1990 年之后';
  }
  return errors;
}

export function buildPetUpdate(draft: PetDraft): PetUpdate {
  return {
    name: draft.name.trim(),
    species: draft.species,
    breed: draft.breed.trim(),
    avatarEmoji: draft.avatarEmoji,
    avatarMode: draft.avatarMode,
    avatarTemplateId: draft.avatarTemplateId,
    ...(draft.avatarMode === 'photo' && draft.photo ? { photo: draft.photo } : {}),
    birthDate: draft.birthDate.trim() || null,
  };
}

export function isPetDraftDirty(pet: SocialPet, draft: PetDraft): boolean {
  return (
    draft.name.trim() !== pet.name ||
    draft.breed.trim() !== (pet.breed ?? '') ||
    draft.species !== pet.species ||
    draft.avatarEmoji !== pet.avatar_emoji ||
    draft.avatarMode === 'photo' ||
    (draft.avatarMode === 'emoji' && !!pet.avatarImageUrl) ||
    draft.avatarTemplateId !== (pet.avatar_template_id ?? null) ||
    draft.birthDate.trim() !== (pet.birthDate ?? '')
  );
}
