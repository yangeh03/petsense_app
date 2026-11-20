import { FlatList, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { DOG_MAP, dogMapPointName } from '@/data/dog-map';
import { useTheme } from '@/hooks/use-theme';

import type { DogMapPoint } from '@/types/dog-map';

/** 全量路段列表：不依赖底图，底图失败时仍可浏览全部评分。 */
export function DogMapList({
  selected,
  onSelect,
}: {
  selected: number | null;
  onSelect: (index: number) => void;
}) {
  const theme = useTheme();
  const renderRow = ({ item, index }: { item: DogMapPoint; index: number }) => {
    const name = dogMapPointName(item);
    const tags = item.tags.join(' · ');
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${name}，${item.score} 分，${item.grade}`}
        accessibilityState={{ selected: selected === index }}
        onPress={() => onSelect(index)}
        style={[
          styles.row,
          selected === index && { backgroundColor: theme.tintBackground },
          index > 0 && {
            borderTopWidth: StyleSheet.hairlineWidth,
            borderTopColor: theme.backgroundSelected,
          },
        ]}
      >
        <View style={styles.leading}>
          <ThemedText type="smallBold">{name}</ThemedText>
          <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
            {item.grade}
            {tags ? ` · ${tags}` : ''}
          </ThemedText>
        </View>
        <View
          style={[
            styles.score,
            { backgroundColor: selected === index ? theme.tint : theme.tintBackground },
          ]}
        >
          <ThemedText
            type="smallBold"
            style={{ color: selected === index ? theme.background : theme.tint }}
          >
            {item.score} 分
          </ThemedText>
        </View>
      </Pressable>
    );
  };
  return (
    <FlatList
      data={DOG_MAP.points}
      keyExtractor={(_, index) => String(index)}
      renderItem={renderRow}
      initialNumToRender={14}
      maxToRenderPerBatch={14}
      style={styles.list}
      scrollEnabled={false}
      nestedScrollEnabled
    />
  );
}
const styles = StyleSheet.create({
  list: { width: '100%' },
  row: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    paddingVertical: 10,
  },
  leading: { flex: 1, gap: 2 },
  score: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
});
