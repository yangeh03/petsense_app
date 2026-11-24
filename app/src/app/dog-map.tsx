import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { DogMapDetail } from '@/components/dog-map/dog-map-detail';
import { DogMapList } from '@/components/dog-map/dog-map-list';
import { DogMapMap } from '@/components/dog-map/dog-map-map';
import type { DogMapFocus } from '@/components/dog-map/dog-map-surface.types';
import { MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { DOG_MAP } from '@/data/dog-map';
import { useTheme } from '@/hooks/use-theme';

/**
 * 遛狗地图：本地打包的南沙路段评分快照，独立于项圈与实时遥测；
 * 返回即回到位置页的室外视图。列表点选后详情以底部卡片就近展示，不依赖底图。
 */
export default function DogMapScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [view, setView] = useState<'map' | 'list'>('map');
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<DogMapFocus | null>(null);
  const [dragging, setDragging] = useState(false);
  const selectedPoint = selected === null ? null : (DOG_MAP.points[selected] ?? null);
  const selectFromList = (index: number) => {
    setSelected(index);
    setFocus({ index, request: Date.now() });
  };
  return (
    <View style={styles.screen}>
      <ScrollView
        scrollEnabled={!dragging}
        style={{ backgroundColor: theme.background }}
        contentContainerStyle={styles.scroll}
        contentInsetAdjustmentBehavior="automatic"
      >
        <View style={[styles.container, { paddingBottom: insets.bottom + Spacing.six }]}>
          <View style={styles.headRow}>
            <View style={styles.headText}>
              <ThemedText style={styles.sectionTitle}>广州南沙</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                已有路段评分 · 离线快照 {DOG_MAP.capturedAt}
              </ThemedText>
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ selected: view === 'list' }}
              onPress={() => setView(view === 'map' ? 'list' : 'map')}
              style={[styles.toggle, { backgroundColor: theme.backgroundSelected }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                {view === 'map' ? '列表' : '地图'}
              </ThemedText>
            </Pressable>
          </View>
          <DogMapMap
            hidden={view !== 'map'}
            focus={focus}
            selected={selected}
            onSelect={setSelected}
            onDragging={setDragging}
          />
          {view === 'list' && <DogMapList selected={selected} onSelect={selectFromList} />}
          {view === 'map' && <DogMapDetail point={selectedPoint} />}
          <View style={[styles.footer, { borderTopColor: theme.backgroundSelected }]}>
            <ThemedText type="small" themeColor="textSecondary">
              评分来源 · {DOG_MAP.source.name}（{DOG_MAP.capturedAt}{' '}
              抓取，原始采集时间未知）。坐标已从 GCJ-02 转换为 WGS-84
              近似值；评分与等级保留源数据，不实时更新。底图 © OpenStreetMap contributors。
            </ThemedText>
          </View>
        </View>
      </ScrollView>
      {/* 列表模式下选中详情固定在底部，点选即可见，无需滚动或切换视图。 */}
      {view === 'list' && selectedPoint && (
        <View
          style={[styles.sheet, { paddingBottom: insets.bottom + Spacing.two }]}
          pointerEvents="box-none"
        >
          <View
            accessibilityLiveRegion="polite"
            style={[
              styles.sheetCard,
              { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
            ]}
          >
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="关闭详情"
              onPress={() => setSelected(null)}
              style={styles.sheetClose}
            >
              <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                关闭
              </ThemedText>
            </Pressable>
            <DogMapDetail point={selectedPoint} />
          </View>
        </View>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: 'transparent' },
  scroll: { alignItems: 'center' },
  container: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.four,
    gap: Spacing.three,
  },
  headRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  headText: { flex: 1, gap: 2 },
  sectionTitle: { fontSize: 20, lineHeight: 28, fontWeight: '700' },
  toggle: { borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8 },
  footer: { borderTopWidth: 1, paddingTop: Spacing.three, gap: Spacing.one },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: Spacing.four,
  },
  sheetCard: {
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
    borderRadius: 16,
    borderWidth: 1,
    padding: Spacing.three,
    paddingRight: 64,
    boxShadow: Shadows.card,
  },
  sheetClose: {
    position: 'absolute',
    top: Spacing.two,
    right: Spacing.three,
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
