import { NativeTabs } from 'expo-router/native-tabs';
import { Platform, useColorScheme } from 'react-native';

import { Colors } from '@/constants/theme';
import { APP_TABS } from '@/constants/tab-navigation';

/** 底部 Tab 栏：今日、位置、伙伴、社区、我的。 */
export default function AppTabs() {
  const scheme = useColorScheme();
  const colors = Colors[scheme ?? 'light'];

  return (
    <NativeTabs
      backgroundColor={colors.background}
      indicatorColor={colors.tint}
      labelStyle={{ selected: { color: colors.tint } }}
    >
      {APP_TABS.map((tab) => (
        <NativeTabs.Trigger
          key={tab.name}
          name={tab.name}
          // iOS 原生 Tab 默认把安全区顶部自动补进「首个滚动视图」的 contentInset，
          // 与页面自己加的 paddingTop: insets.top 叠加，顶部会多出一条空白带
          // （今日/位置/我的三页最明显）。关掉后安全区一律由页面自己管。
          // 仅 iOS：Android 侧该开关会去掉 react-native-screens 套的底部 SafeAreaView。
          disableAutomaticContentInsets={Platform.OS === 'ios'}
        >
          <NativeTabs.Trigger.Label>{tab.label}</NativeTabs.Trigger.Label>
          <NativeTabs.Trigger.Icon
            sf={{ default: tab.icon.ios, selected: tab.selectedIcon.ios }}
            md={{ default: tab.icon.android, selected: tab.selectedIcon.android }}
            selectedColor={colors.tint}
          />
        </NativeTabs.Trigger>
      ))}
    </NativeTabs>
  );
}
