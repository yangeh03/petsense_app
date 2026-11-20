import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { DOG_MAP, dogMapPointName } from '@/data/dog-map';
import { useTheme } from '@/hooks/use-theme';

/** 全量路段列表：不依赖底图，底图失败时仍可浏览全部评分。点选后详情由页面就近展示。 */
export function DogMapList({
  selected,
  onSelect,
}: {
  selected: number | null;
  onSelect: (index: number) => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.list}>
      {DOG_MAP.points.map((point, index) => {
        const name = dogMapPointName(point);
        const tags = point.tags.join(' · ');
        return (
          <Pressable
            key={index}
            accessibilityRole="button"
            accessibilityLabel={`${name}，${point.score} 分，${point.grade}`}
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
                {point.grade}
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
                {point.score} 分
              </ThemedText>
            </View>
          </Pressable>
        );
      })}
    </View>
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
