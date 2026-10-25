/**
 * PetBehaviorGIF 服务客户端：数字宠物形象 / 行为 GIF / 运动日记 / 角色卡。
 * 全部经腾讯云后端 /api/app/petgif/* 代理，API key 只存服务端。
 */

import { ApiError, loadToken, request } from '@/services/api';
import { API_BASE_URL } from '@/config';
import { buildUploadForm, IS_WEB_LIKE, sendMultipart } from '@/services/form-upload';
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
  const url = `${API_BASE_URL}/api/app/petgif/pet/${petgifId}/diary`;
  const headers = await buildAuthHeaders();
  const jsonBody = JSON.stringify({
    durationMin: input.durationMin,
    steps: input.steps,
    places: input.places,
    notes: input.notes,
  });
  if (input.photos?.length && !IS_WEB_LIKE) {
    // 原生端 multipart 走 XHR（expo/fetch 不支持 {uri} FormData part）
    const { status, body } = await sendMultipart(url, {
      method: 'POST',
      headers,
      timeoutMs: 240_000,
      fields: {
        durationMin: String(input.durationMin),
        steps: String(input.steps),
        places: input.places,
        notes: input.notes,
      },
      files: input.photos.map((photo) => ({ field: 'photos', ...photo })),
    });
    try {
      return JSON.parse(body) as {
        posterUrl?: string | null;
        status: string;
        message?: string;
      };
    } catch {
      return { status: 'failed', message: `生成失败（${status}）` };
    }
  }
  let body: BodyInit;
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
    body = jsonBody;
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240_000);
  try {
    const response = await fetch(url, {
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
  const url = `${API_BASE_URL}/api/app/petgif/pets/sync`;
  const headers = await buildAuthHeaders();
  // 后端最长阻塞 150s，客户端给 200s 余量
  if (!IS_WEB_LIKE) {
    // 原生端 multipart 走 XHR（expo/fetch 不支持 {uri} FormData part）
    const { status, body } = await sendMultipart(url, {
      method: 'POST',
      headers,
      timeoutMs: 200_000,
      fields: { name: input.name },
      files: [{ field: 'photo', ...input.image }],
    });
    let payload: {
      success?: boolean;
      petgifId?: string;
      message?: string;
      pet?: PetgifPet;
    } = {};
    try {
      payload = JSON.parse(body);
    } catch {
      /* 保持空载荷，走下方统一错误 */
    }
    if (status < 200 || (status >= 300 && status !== 202)) {
      throw new ApiError(payload.message ?? `生成失败（${status}）`, status);
    }
    if (!payload.petgifId) {
      throw new ApiError(payload.message ?? '生成失败', 0);
    }
    return { petgifId: payload.petgifId, characterCardUrl: payload.pet?.characterCardUrl ?? null };
  }
  const data = await buildUploadForm({ name: input.name }, [{ field: 'photo', ...input.image }]);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 200_000);
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers,
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
