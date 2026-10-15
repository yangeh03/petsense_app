/**
 * 实时行为推断：把遥测帧映射为宠物当前动作（run/walk/stand/sit/lie_down…），
 * 供 Dashboard Hero 切换 PetGIF 行为动图。优先级：
 * 1. 实验标注事件（研究者正在标记的真实行为）
 * 2. 云端 1 秒随机森林预测
 * 3. IMU 加速度合幅值分档（剧烈→run，中→walk）
 * 4. 静止兜底：夜间 sleep、白天 stand
 */

import { useMemo } from 'react';

import type { TelemetryFrame } from '@/types/telemetry';

export type PetActionKey =
  'run' | 'walk' | 'stand' | 'sit' | 'lie_down' | 'sniff' | 'jump' | 'eat' | 'sleep';

/** 实验标注事件 id/中文标签 → 动作 key */
const EVENT_TO_ACTION: Record<string, PetActionKey> = {
  '01_standing': 'stand',
  '02_sitting': 'sit',
  '03_resting': 'lie_down',
  '04_sleeping': 'sleep',
  '05_walking': 'walk',
  '06_running': 'run',
  '07_eating': 'eat',
  '08_sniffing': 'sniff',
  '08_rolling': 'sniff',
  '09_toilet': 'stand',
  '10_jumping': 'jump',
  站立: 'stand',
  坐下: 'sit',
  '趴下 / 休息': 'lie_down',
  趴下: 'lie_down',
  睡觉: 'sleep',
  走路: 'walk',
  跑步: 'run',
  吃东西: 'eat',
  嗅闻: 'sniff',
  打滚: 'sniff',
  上厕所: 'stand',
  跳跃: 'jump',
};

const RUN_THRESHOLD_G = 0.32;
const WALK_THRESHOLD_G = 0.16;

export function inferPetAction(
  latest: TelemetryFrame | null | undefined,
  now = new Date(),
): PetActionKey {
  // 1) 实验标注中的真实行为
  const experiment = latest?.experiment;
  if (experiment?.eventActive) {
    const raw = `${experiment.eventId ?? ''}\u0000${experiment.eventLabel ?? ''}`.split('\u0000');
    for (const token of raw) {
      const mapped = EVENT_TO_ACTION[token.trim()];
      if (mapped) return mapped;
    }
  }

  // 2) 云端模型预测（只接受 APP 已知的动作 key）
  const modelAction = latest?.action?.actionKey;
  if (modelAction && Object.values(EVENT_TO_ACTION).includes(modelAction as PetActionKey)) {
    return modelAction as PetActionKey;
  }

  // 3) IMU 加速度合幅值分档
  const accel = latest?.imu?.ok ? latest.imu.accel : null;
  if (accel && accel.length === 3 && accel.every((v) => Number.isFinite(v))) {
    const magnitude = Math.hypot(accel[0], accel[1], accel[2]);
    const delta = Math.abs(magnitude - 1);
    if (delta > RUN_THRESHOLD_G) return 'run';
    if (delta > WALK_THRESHOLD_G) return 'walk';
  }

  // 4) 静止兜底：夜间睡觉、白天站立
  const hour = now.getHours();
  return hour >= 22 || hour < 8 ? 'sleep' : 'stand';
}

/** usePetAction：跟随最新遥测帧推断动作（memoized） */
export function usePetAction(latest: TelemetryFrame | null | undefined): PetActionKey {
  const accel = latest?.imu?.ok ? latest.imu.accel.join(',') : '';
  const eventKey = latest?.experiment?.eventActive
    ? `${latest.experiment.eventId}|${latest.experiment.eventLabel}`
    : '';
  const modelAction = latest?.action
    ? `${latest.action.actionKey}|${latest.action.windowEndTsMs}`
    : '';
  // accel/eventKey 是 latest 的稳定派生键：帧对象引用每次都变，但只有这两个字段影响结果
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => inferPetAction(latest), [accel, eventKey, modelAction]);
}
