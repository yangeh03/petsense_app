import { SymbolView } from 'expo-symbols';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

type TaskRowProps = {
  title: string;
  time: string;
  done: boolean;
  onToggle: () => void;
};

/** 今日护理待办，点击圆形按钮切换完成状态。 */
export function TaskRow({ title, time, done, onToggle }: TaskRowProps) {
  const theme = useTheme();

  return (
    <Pressable onPress={onToggle} style={({ pressed }) => [styles.row, pressed && styles.pressed]}>
      <View
        style={[
          styles.checkbox,
          { borderColor: done ? theme.tint : theme.textSecondary },
          done && { backgroundColor: theme.tint },
        ]}
      >
        {done && (
          <SymbolView
            name={{ ios: 'checkmark', android: 'check', web: 'check' }}
            size={12}
            weight="bold"
            tintColor="#ffffff"
          />
        )}
      </View>
      <ThemedText
        type="default"
        style={[styles.title, done && styles.titleDone]}
        themeColor={done ? 'textSecondary' : 'text'}
      >
        {title}
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {time}
      </ThemedText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: Spacing.two,
    gap: Spacing.three,
  },
  pressed: {
    opacity: 0.7,
  },
  checkbox: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    marginRight: 'auto',
  },
  titleDone: {
    textDecorationLine: 'line-through',
  },
});
