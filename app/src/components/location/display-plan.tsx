import { Image } from 'expo-image';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import type { DisplayPlan } from '@/services/display-plan';

export function DisplayPlanCard({ plan }: { plan: DisplayPlan }) {
  const theme = useTheme();
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  return (
    <View style={styles.content}>
      <ThemedText type="smallBold">{plan.name}</ThemedText>
      <Image
        key={attempt}
        source={{ uri: plan.image.imageDataUri }}
        contentFit="contain"
        style={{ width: '100%', aspectRatio: plan.image.width / plan.image.height }}
        onError={() => setFailed(true)}
        accessibilityLabel="已确认的彩色场地底图"
      />
      {failed && (
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            setFailed(false);
            setAttempt((x) => x + 1);
          }}
        >
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            底图显示失败，点击重试
          </ThemedText>
        </Pressable>
      )}
      <ThemedText type="small" themeColor="textSecondary">
        底图已确认 · 待定位标定
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        完成基站标定和场地对齐后，即可在图上显示项圈位置。
      </ThemedText>
    </View>
  );
}
const styles = StyleSheet.create({ content: { width: '100%', gap: 12 } });
