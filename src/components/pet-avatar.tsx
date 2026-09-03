import { StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

type PetAvatarProps = {
  emoji: string;
  size?: number;
};

/** 宠物头像：品牌色圆形背景 + emoji，避免引入图片资源。 */
export function PetAvatar({ emoji, size = 56 }: PetAvatarProps) {
  const theme = useTheme();

  return (
    <View
      style={[
        styles.avatar,
        {
          backgroundColor: theme.tintBackground,
          width: size,
          height: size,
          borderRadius: size / 2,
        },
      ]}
    >
      <ThemedText style={{ fontSize: size * 0.5 }}>{emoji}</ThemedText>
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: 'rgba(128, 128, 128, 0.2)',
    marginRight: Spacing.three,
  },
});
