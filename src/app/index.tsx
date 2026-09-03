import { SymbolView } from 'expo-symbols';
import { useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ActivityBar } from '@/components/activity-bar';
import { PetAvatar } from '@/components/pet-avatar';
import { TaskRow } from '@/components/task-row';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useCurrentPet } from '@/context/current-pet';
import { activityMetrics, careTasks, speciesLabel } from '@/data/pets';
import { useTheme } from '@/hooks/use-theme';

/** 首页：当前宠物的今日健康概览。 */
export default function HomeScreen() {
  const theme = useTheme();
  const { currentPet } = useCurrentPet();
  const insets = useSafeAreaInsets();
  const [tasks, setTasks] = useState(careTasks);

  const doneCount = tasks.filter((task) => task.done).length;

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        <View style={styles.header}>
          <View style={styles.headerText}>
            <ThemedText type="small" themeColor="textSecondary">
              9 月 3 日 · 星期四
            </ThemedText>
            <ThemedText type="subtitle">今日</ThemedText>
          </View>
          <SymbolView
            name={{ ios: 'bell.badge', android: 'notifications', web: 'notifications' }}
            size={22}
            tintColor={theme.text}
          />
        </View>

        {/* 当前宠物卡片 */}
        <ThemedView type="backgroundElement" style={styles.card}>
          <View style={styles.petRow}>
            <PetAvatar emoji={currentPet.emoji} size={56} />
            <View style={styles.petInfo}>
              <View style={styles.petNameRow}>
                <ThemedText type="smallBold" style={styles.petName}>
                  {currentPet.name}
                </ThemedText>
                <View style={[styles.tag, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    {speciesLabel[currentPet.species]}
                  </ThemedText>
                </View>
              </View>
              <ThemedText type="small" themeColor="textSecondary" style={styles.petBio}>
                {currentPet.bio}
              </ThemedText>
            </View>
          </View>

          <View style={[styles.statRow, { gap: Spacing.three }]}>
            <View style={styles.statItem}>
              <ThemedText type="smallBold">{currentPet.age} 岁</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                年龄
              </ThemedText>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <ThemedText type="smallBold">{currentPet.weight} kg</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                体重
              </ThemedText>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <ThemedText
                type="smallBold"
                style={{ color: currentPet.vaccinated ? theme.tint : theme.warning }}
              >
                {currentPet.vaccinated ? '已齐全' : '待补种'}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                疫苗
              </ThemedText>
            </View>
          </View>
        </ThemedView>

        {/* 今日活动 */}
        <View style={styles.sectionHeader}>
          <ThemedText type="smallBold">今日活动</ThemedText>
        </View>
        <ThemedView type="backgroundElement" style={styles.card}>
          <View style={styles.activityList}>
            {activityMetrics.map((metric) => (
              <ActivityBar key={metric.id} metric={metric} />
            ))}
          </View>
        </ThemedView>

        {/* 今日护理待办 */}
        <View style={styles.sectionHeader}>
          <ThemedText type="smallBold">今日护理</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            已完成 {doneCount}/{tasks.length}
          </ThemedText>
        </View>
        <ThemedView type="backgroundElement" style={styles.card}>
          {tasks.map((task) => (
            <TaskRow
              key={task.id}
              title={task.title}
              time={task.time}
              done={task.done}
              onToggle={() =>
                setTasks((prev) =>
                  prev.map((item) => (item.id === task.id ? { ...item, done: !item.done } : item)),
                )
              }
            />
          ))}
        </ThemedView>

        <ThemedText type="small" themeColor="textSecondary" style={styles.footer}>
          数据为演示用 Mock 数据 · 可在「宠物」页切换当前宠物
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
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerText: {
    gap: Spacing.one,
  },
  card: {
    borderRadius: Spacing.three,
    padding: Spacing.three,
    gap: Spacing.three,
  },
  petRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  petInfo: {
    flex: 1,
    gap: Spacing.one,
  },
  petNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  petName: {
    fontSize: 18,
  },
  petBio: {
    flexShrink: 1,
  },
  tag: {
    borderRadius: Spacing.two,
    paddingHorizontal: Spacing.two,
    paddingVertical: 2,
  },
  statRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-evenly',
  },
  statItem: {
    alignItems: 'center',
    gap: Spacing.one,
  },
  statDivider: {
    width: StyleSheet.hairlineWidth,
    alignSelf: 'stretch',
    backgroundColor: 'rgba(128, 128, 128, 0.3)',
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    marginTop: Spacing.two,
  },
  activityList: {
    gap: Spacing.three,
  },
  footer: {
    textAlign: 'center',
    marginTop: Spacing.three,
  },
});
