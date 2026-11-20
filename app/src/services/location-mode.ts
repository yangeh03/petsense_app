import AsyncStorage from '@react-native-async-storage/async-storage';

/** 位置页的显式室内/室外选择；默认室内，且绝不随遥测自动切换。 */
export type LocationMode = 'indoor' | 'outdoor';

export const LOCATION_MODE_KEY = 'petsense.location.mode';

/** 非法或缺失的存储值一律回落到室内。 */
export function parseLocationMode(raw: string | null | undefined): LocationMode {
  return raw === 'outdoor' ? 'outdoor' : 'indoor';
}

export async function loadLocationMode(): Promise<LocationMode> {
  try {
    return parseLocationMode(await AsyncStorage.getItem(LOCATION_MODE_KEY));
  } catch {
    return 'indoor';
  }
}

export async function saveLocationMode(mode: LocationMode): Promise<void> {
  try {
    await AsyncStorage.setItem(LOCATION_MODE_KEY, mode);
  } catch {
    // 存储失败不阻断使用：本次会话仍保留显式选择。
  }
}
