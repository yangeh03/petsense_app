import { NativeTabs } from 'expo-router/unstable-native-tabs';
import { useColorScheme } from 'react-native';

import { Colors } from '@/constants/theme';
import { APP_TABS } from '@/constants/tab-navigation';

/** 底部 Tab 栏：今日、记录、位置、社区、我的。 */
export default function AppTabs() {
  const scheme = useColorScheme();
  const colors = Colors[scheme === 'unspecified' ? 'light' : scheme];

  return (
    <NativeTabs
      backgroundColor={colors.background}
      indicatorColor={colors.tint}
      labelStyle={{ selected: { color: colors.tint } }}
    >
      {APP_TABS.map((tab) => (
        <NativeTabs.Trigger key={tab.name} name={tab.name}>
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
