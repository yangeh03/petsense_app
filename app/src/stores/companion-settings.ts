/**
 * W03 助手设定缓存（agent-w03-spec.md §4.1）：模块级 zustand 单例。
 * 伙伴页三处助手显示名（页头副标题、指向选择、消息标签）与设定页共用，
 * 聊天内直写后经 refresh() 拉齐，改名即时生效。
 */

import { create } from 'zustand';

import { fetchAssistantSettings, type CompanionSettings } from '@/services/api';

const DEFAULTS: CompanionSettings = {
  assistantName: '照料助手',
  ownerTitle: null,
  verbosity: 'concise',
  proactivity: 1,
};

type CompanionSettingsState = {
  settings: CompanionSettings;
  loaded: boolean;
  refresh: () => Promise<void>;
};

export const useCompanionSettingsStore = create<CompanionSettingsState>((set) => ({
  settings: DEFAULTS,
  loaded: false,
  refresh: async () => {
    try {
      const data = await fetchAssistantSettings();
      set({ settings: { ...DEFAULTS, ...data.settings }, loaded: true });
    } catch {
      // 拉取失败保持当前值（含默认名），不阻塞伙伴页
    }
  },
}));

/** 助手显示名（未加载时用默认「照料助手」）。 */
export function assistantDisplayName(settings: CompanionSettings): string {
  return settings.assistantName?.trim() || DEFAULTS.assistantName;
}
