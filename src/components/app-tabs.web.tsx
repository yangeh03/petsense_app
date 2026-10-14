import { SymbolView } from 'expo-symbols';
import { Tabs, TabList, TabSlot, TabTrigger } from 'expo-router/ui';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

const TABS = [
  {
    name: 'index',
    href: '/',
    label: '今日',
    icon: { ios: 'house.fill', android: 'home', web: 'home' },
  },
  {
    name: 'tracking',
    href: '/tracking',
    label: '位置',
    icon: { ios: 'mappin.and.ellipse', android: 'place', web: 'place' },
  },
  {
    name: 'community',
    href: '/community',
    label: '社区',
    icon: { ios: 'person.2', android: 'people', web: 'people' },
  },
  {
    name: 'profile',
    href: '/profile',
    label: '我的',
    icon: { ios: 'person', android: 'person', web: 'person' },
  },
] as const;

/**
 * Web 端底部 Tab 栏：内容出口 TabSlot + 四入口均分，宽裕不拥挤。
 * 创建入口由 (tabs)/_layout 的右下角悬浮按钮承担，不占用底栏空间。
 */
export default function AppTabs() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/';
  const isActive = (href: string) =>
    pathname === href || (href !== '/' && pathname.startsWith(href));
  // TabList asChild 的子元素不能接收样式数组，需压平
  const barStyle = StyleSheet.flatten([
    styles.bar,
    {
      backgroundColor: theme.background,
      paddingBottom: Math.max(insets.bottom, 6),
      borderTopColor: 'rgba(128, 110, 90, 0.25)',
    },
  ]);

  return (
    <Tabs>
      <TabSlot style={{ height: '100%' }} />
      <TabList asChild>
        <View style={barStyle}>
          {TABS.map((tab) => (
            <TabTrigger key={tab.name} name={tab.name} href={tab.href} asChild>
              <TabButton label={tab.label} icon={tab.icon} active={isActive(tab.href)} />
            </TabTrigger>
          ))}
        </View>
      </TabList>
    </Tabs>
  );
}

function TabButton({
  label,
  icon,
  active,
  onPress,
}: {
  label: string;
  icon: (typeof TABS)[number]['icon'];
  active: boolean;
  onPress?: () => void;
}) {
  const theme = useTheme();
  const color = active ? theme.tint : theme.textSecondary;
  return (
    <Pressable onPress={onPress} style={styles.tab}>
      <SymbolView name={icon} size={22} tintColor={color} />
      <ThemedText type="small" style={{ color, fontSize: 11, lineHeight: 14 }}>
        {label}
      </ThemedText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: Spacing.one,
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
    paddingVertical: Spacing.one,
  },
});
