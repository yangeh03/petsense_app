/**
 * PetBehaviorGIF 服务客户端：数字宠物形象 / 行为 GIF / 运动日记 / 角色卡。
 * 全部经腾讯云后端 /api/app/petgif/* 代理，API key 只存服务端。
 */

import { request } from '@/services/api';
import type { PetgifPet } from '@/types/social';

export type { PetgifPet } from '@/types/social';

export const PETGIF_DEFAULT_ID = 'default';

export function fetchPetgifPet(petgifId: string): Promise<PetgifPet> {
  return request(`/petgif/pet/${petgifId}`);
}

/** 生成运动日记海报（后端最长轮询 90 秒） */
export function generateDiary(
  petgifId: string,
  input: { durationMin: number; steps: number; places: string; notes: string },
): Promise<{ posterUrl?: string | null; status: string }> {
  return request(`/petgif/pet/${petgifId}/diary`, { method: 'POST', body: input });
}

/** 把生成的日记海报发到社区 */
export function shareDiaryPost(input: {
  posterUrl: string;
  caption: string;
  location?: string;
  petId?: number;
}): Promise<{ postId: number }> {
  return request('/petgif/diary-post', { method: 'POST', body: input });
}

/** 上传宠物照片生成专属数字形象（返回 petgifId，需轮询状态） */
export function createPetgifPet(input: {
  image: { uri: string; name: string; type: string };
  name: string;
}): Promise<{ id: string; status?: string }> {
  const data = new FormData();
  data.append('photo', {
    uri: input.image.uri,
    name: input.image.name,
    type: input.image.type,
  } as unknown as string);
  data.append('name', input.name);
  return request('/petgif/pets', { method: 'POST', form: data });
}

/** 绑定 PetGIF 形象到宠物档案 */
export function bindPetgif(petId: number, petgifId: string): Promise<{ success: boolean }> {
  return request(`/pets/${petId}/petgif`, { method: 'POST', body: { petgifId } });
}

/** 把生成的角色卡设为宠物头像 */
export function setPetAvatarImage(
  petId: number,
  imageUrl: string,
): Promise<{ avatarImageUrl: string }> {
  return request(`/pets/${petId}/avatar-image`, { method: 'POST', body: { imageUrl } });
}
