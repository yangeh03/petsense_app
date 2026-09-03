import { NativeTabs } from 'expo-router/unstable-native-tabs';
import { useColorScheme } from 'react-native';

import { Colors } from '@/constants/theme';

/** 底部 Tab 栏：iOS / Android 原生渲染，图标使用系统符号。 */
export default function AppTabs() {
  const scheme = useColorScheme();
  const colors = Colors[scheme === 'unspecified' ? 'light' : scheme];

  return (
    <NativeTabs
      backgroundColor={colors.background}
      indicatorColor={colors.tint}
      labelStyle={{ selected: { color: colors.tint } }}
    >
      <NativeTabs.Trigger name="index">
        <NativeTabs.Trigger.Label>今日</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon
          sf={{ default: 'house', selected: 'house.fill' }}
          md={{ default: 'home', selected: 'home_filled' }}
          selectedColor={colors.tint}
        />
      </NativeTabs.Trigger>

      <NativeTabs.Trigger name="pets">
        <NativeTabs.Trigger.Label>宠物</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon
          sf={{ default: 'pawprint', selected: 'pawprint.fill' }}
          md="pets"
          selectedColor={colors.tint}
        />
      </NativeTabs.Trigger>

      <NativeTabs.Trigger name="profile">
        <NativeTabs.Trigger.Label>我的</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon
          sf={{ default: 'person', selected: 'person.fill' }}
          md="person"
          selectedColor={colors.tint}
        />
      </NativeTabs.Trigger>
    </NativeTabs>
  );
}
