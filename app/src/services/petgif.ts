/**
 * PetBehaviorGIF 服务客户端：数字宠物形象 / 行为 GIF / 运动日记 / 角色卡。
 * 全部经腾讯云后端 /api/app/petgif/* 代理，API key 只存服务端。
 */

import { ApiError, loadToken, request } from '@/services/api';
import { API_BASE_URL } from '@/config';
import { buildUploadForm } from '@/services/form-upload';
import type { PetgifPet } from '@/types/social';

async function buildAuthHeaders(): Promise<Record<string, string>> {
  const token = await loadToken();
  return token ? { 'X-Petsense-Session': token } : {};
}

export type { PetgifPet } from '@/types/social';

export const PETGIF_DEFAULT_ID = 'default';

export function fetchPetgifPet(petgifId: string): Promise<PetgifPet> {
  return request(`/petgif/pet/${petgifId}`);
}

/** 生成运动日记海报（外部全流程约 2 分钟；后端轮询 200s，客户端 240s 超时兜底） */
export async function generateDiary(
  petgifId: string,
  input: {
    durationMin: number;
    steps: number;
    places: string;
    notes: string;
    photos?: { uri: string; name: string; type: string }[];
  },
): Promise<{ posterUrl?: string | null; status: string; message?: string }> {
  let body: BodyInit;
  const headers = await buildAuthHeaders();
  if (input.photos?.length) {
    body = await buildUploadForm(
      {
        durationMin: String(input.durationMin),
        steps: String(input.steps),
        places: input.places,
        notes: input.notes,
      },
      input.photos.map((photo) => ({ field: 'photos', ...photo })),
    );
  } else {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify({
      durationMin: input.durationMin,
      steps: input.steps,
      places: input.places,
      notes: input.notes,
    });
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/app/petgif/pet/${petgifId}/diary`, {
      method: 'POST',
      headers,
      body,
      signal: controller.signal,
    });
    return (await response.json()) as {
      posterUrl?: string | null;
      status: string;
      message?: string;
    };
  } finally {
    clearTimeout(timeout);
  }
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

/** 上传宠物照片生成专属数字形象（同步版：阻塞至角色卡就绪，约 1 分钟） */
export async function createPetgifPetSync(input: {
  image: { uri: string; name: string; type: string };
  name: string;
}): Promise<{ petgifId: string; characterCardUrl?: string | null }> {
  const data = await buildUploadForm({ name: input.name }, [{ field: 'photo', ...input.image }]);
  // 后端最长阻塞 150s，客户端给 200s 余量
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 200_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/app/petgif/pets/sync`, {
      method: 'POST',
      headers: await buildAuthHeaders(),
      body: data,
      signal: controller.signal,
    });
    const payload = (await response.json()) as {
      success: boolean;
      petgifId?: string;
      message?: string;
      pet?: PetgifPet;
    };
    if (!response.ok && response.status !== 202) {
      throw new ApiError(payload.message ?? `生成失败（${response.status}）`, response.status);
    }
    if (!payload.petgifId) {
      throw new ApiError(payload.message ?? '生成失败', 0);
    }
    return { petgifId: payload.petgifId, characterCardUrl: payload.pet?.characterCardUrl ?? null };
  } finally {
    clearTimeout(timeout);
  }
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
