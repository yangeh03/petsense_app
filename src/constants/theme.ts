/**
 * 暖珊瑚主题（对齐设计稿）：奶油底色 + 珊瑚橙主色 + 深棕文字，
 * 绿色只保留给 Online / 正常等语义状态。
 */

import '@/global.css';

import { Platform } from 'react-native';

export const Colors = {
  light: {
    text: '#3A2E26',
    background: '#FBF9F4',
    backgroundElement: '#FFFFFF',
    backgroundSelected: '#F3EAE0',
    textSecondary: '#94836F',
    /** 品牌主色：珊瑚橙 */
    tint: '#E8835C',
    /** 品牌主色的浅色背景，用于标签、卡片点缀 */
    tintBackground: '#FBEBE1',
    /** 提醒 / 未完成 */
    warning: '#E8A13A',
    /** 告警严重级（critical） */
    danger: '#E5484D',
    /** 告警提示级（数据失联等） */
    info: '#5B8DEF',
    /** 在线 / 正常状态 */
    online: '#31A66D',
  },
  dark: {
    text: '#F3EAE0',
    background: '#17120E',
    backgroundElement: '#241D17',
    backgroundSelected: '#33291F',
    textSecondary: '#B5A390',
    tint: '#F0987A',
    tintBackground: '#4A2A1D',
    warning: '#F0BC6B',
    danger: '#FF6369',
    info: '#7FA8F2',
    online: '#4CC38A',
  },
} as const;

export type ThemeColor = keyof typeof Colors.light & keyof typeof Colors.dark;

export const Fonts = Platform.select({
  ios: {
    /** iOS `UIFontDescriptorSystemDesignDefault` */
    sans: 'system-ui',
    /** iOS `UIFontDescriptorSystemDesignSerif` */
    serif: 'ui-serif',
    /** iOS `UIFontDescriptorSystemDesignRounded` */
    rounded: 'ui-rounded',
    /** iOS `UIFontDescriptorSystemDesignMonospaced` */
    mono: 'ui-monospace',
  },
  default: {
    sans: 'normal',
    serif: 'serif',
    rounded: 'normal',
    mono: 'monospace',
  },
  web: {
    sans: 'var(--font-display)',
    serif: 'var(--font-serif)',
    rounded: 'var(--font-rounded)',
    mono: 'var(--font-mono)',
  },
});

export const Spacing = {
  half: 2,
  one: 4,
  two: 8,
  three: 16,
  four: 24,
  five: 32,
  six: 64,
} as const;

export const BottomTabInset = Platform.select({ ios: 50, android: 80 }) ?? 0;
export const MaxContentWidth = 800;

/** 设计稿同款柔和投影（boxShadow 三端通用，避免 shadow* 弃用告警） */
export const Shadows = {
  card: '0 10px 26px rgba(140, 96, 62, 0.10)',
  chip: '0 6px 16px rgba(140, 96, 62, 0.12)',
} as const;
