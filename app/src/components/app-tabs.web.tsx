import { SymbolView } from 'expo-symbols';
import { Tabs, TabList, TabSlot, TabTrigger } from 'expo-router/ui';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { APP_TABS } from '@/constants/tab-navigation';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * Web 端底部 Tab 栏：内容出口 TabSlot + 五入口均分。
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
          {APP_TABS.map((tab) => (
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
  icon: (typeof APP_TABS)[number]['icon'];
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
