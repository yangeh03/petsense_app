import { SymbolView } from 'expo-symbols';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

const menuSections = [
  {
    title: '宠物健康',
    items: [
      {
        id: 'report',
        label: '每周健康报告',
        icon: { ios: 'doc.text.fill', android: 'description', web: 'description' },
      },
      {
        id: 'vaccine',
        label: '疫苗与驱虫提醒',
        icon: { ios: 'syringe.fill', android: 'vaccines', web: 'thermometer' },
      },
      {
        id: 'weight',
        label: '体重趋势',
        icon: { ios: 'chart.line.uptrend.xyaxis', android: 'show_chart', web: 'trending_up' },
      },
    ],
  },
  {
    title: '通用设置',
    items: [
      {
        id: 'notification',
        label: '消息通知',
        icon: { ios: 'bell.badge.fill', android: 'notifications', web: 'notifications' },
      },
      {
        id: 'family',
        label: '家人共享',
        icon: { ios: 'person.2.fill', android: 'group', web: 'group' },
      },
      {
        id: 'help',
        label: '帮助与反馈',
        icon: { ios: 'questionmark.circle.fill', android: 'help', web: 'help' },
      },
    ],
  },
] as const;

/** 我的页面：用户信息与设置入口（当前为静态演示）。 */
export default function ProfileScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        <ThemedText type="subtitle">我的</ThemedText>

        {/* 用户卡片 */}
        <ThemedView type="backgroundElement" style={styles.userCard}>
          <View style={[styles.userAvatar, { backgroundColor: theme.tintBackground }]}>
            <ThemedText style={{ fontSize: 28 }}>🧑‍🌾</ThemedText>
          </View>
          <View style={styles.userInfo}>
            <ThemedText type="smallBold" style={styles.userName}>
              宠物家长小杨
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              已陪伴 3 只宠物 · 426 天
            </ThemedText>
          </View>
        </ThemedView>

        {/* 设置菜单 */}
        {menuSections.map((section) => (
          <View key={section.title} style={styles.section}>
            <ThemedText type="small" themeColor="textSecondary" style={styles.sectionTitle}>
              {section.title}
            </ThemedText>
            <ThemedView type="backgroundElement" style={styles.menuCard}>
              {section.items.map((item) => (
                <Pressable
                  key={item.id}
                  style={({ pressed }) => [styles.menuRow, pressed && styles.pressed]}
                >
                  <SymbolView name={item.icon} size={18} tintColor={theme.tint} />
                  <ThemedText type="default" style={styles.menuLabel}>
                    {item.label}
                  </ThemedText>
                  <SymbolView
                    name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
                    size={14}
                    tintColor={theme.textSecondary}
                  />
                </Pressable>
              ))}
            </ThemedView>
          </View>
        ))}

        <ThemedText type="small" themeColor="textSecondary" style={styles.footer}>
          PetSense Demo v1.0.0{'\n'}用爱感知每一天 🐾
        </ThemedText>
      </ThemedView>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollView: {
    flex: 1,
  },
  contentContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
  },
  container: {
    flexGrow: 1,
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset + Spacing.five,
    gap: Spacing.three,
  },
  userCard: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: Spacing.three,
    padding: Spacing.three,
    gap: Spacing.three,
    marginTop: Spacing.two,
  },
  userAvatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
  },
  userInfo: {
    gap: Spacing.one,
  },
  userName: {
    fontSize: 18,
  },
  section: {
    gap: Spacing.two,
    marginTop: Spacing.one,
  },
  sectionTitle: {
    paddingLeft: Spacing.one,
  },
  menuCard: {
    borderRadius: Spacing.three,
    paddingHorizontal: Spacing.three,
  },
  menuRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: Spacing.three,
    gap: Spacing.three,
  },
  menuLabel: {
    marginRight: 'auto',
  },
  pressed: {
    opacity: 0.7,
  },
  footer: {
    textAlign: 'center',
    marginTop: Spacing.four,
    lineHeight: 20,
  },
});
