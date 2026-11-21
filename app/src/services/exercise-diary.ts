import { File, Paths } from 'expo-file-system';
import { Platform } from 'react-native';

import { API_BASE_URL } from '@/config';
import { loadToken, request } from '@/services/api';
import type { PetgifDiaryJob } from '@/services/petgif';

export type DiaryMode = 'template' | 'ai';
export type ExerciseDiary = PetgifDiaryJob & { date: string; mode: DiaryMode };
export type DiaryCollection = { date: string; aiAvailable: boolean; jobs: ExerciseDiary[] };

export function listExerciseDiaries(petId: number, signal?: AbortSignal) {
  return request<DiaryCollection>(`/exercise-diaries/${petId}`, { signal });
}

export function createExerciseDiary(petId: number, replaceJobId?: string) {
  return request<ExerciseDiary>(`/exercise-diaries/${petId}`, {
    method: 'POST',
    body: { mode: 'ai', replaceJobId },
  });
}

export async function downloadDiaryPoster(jobId: string, destination?: File) {
  const token = await loadToken();
  if (!token) throw new Error('请先登录');
  const url = `${API_BASE_URL}/api/app/exercise-diaries/poster/${encodeURIComponent(jobId)}`;
  const headers = { 'X-Petsense-Session': token };
  if (Platform.OS === 'web') {
    const response = await fetch(url, { headers });
    if (!response.ok) throw new Error('海报暂时无法下载');
    return URL.createObjectURL(await response.blob());
  }
  const file = await File.downloadFileAsync(
    url,
    destination ?? new File(Paths.cache, `petsense-diary-${jobId}.png`),
    { headers, idempotent: true },
  );
  return file.uri;
}
