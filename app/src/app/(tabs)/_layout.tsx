import { useRouter } from 'expo-router';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import AppTabs from '../../components/app-tabs';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * Tab 组布局：四个页面；原生端右下角浮动 ⊕ 创建按钮（Web 端 Tab 栏已带中央 ⊕）。
 * 按钮悬浮在 Tab 栏上方、与其完全分离，避免挤压底栏。
 */
export default function TabLayout() {
  const theme = useTheme();
  const router = useRouter();
  const fabBottom = Platform.OS === 'web' ? 84 : 104;

  return (
    <View style={styles.container}>
      <AppTabs />
      <Pressable
        onPress={() => router.push('/create')}
        style={[styles.createButton, { backgroundColor: theme.tint, bottom: fabBottom }]}
        accessibilityLabel="创建"
      >
        <Text style={styles.createIcon}>＋</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  createButton: {
    position: 'absolute',
    right: Spacing.four,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#3A2E26',
    shadowOpacity: 0.3,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
    zIndex: 10,
  },
  createIcon: {
    color: '#ffffff',
    fontSize: 28,
    fontWeight: '600',
    lineHeight: 32,
  },
});
