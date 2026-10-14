import { StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { useAlerts, worstSeverity } from '@/hooks/use-alerts';

/**
 * 宠物头像状态环：环色随最严重活跃告警变化（绿正常 / 琥珀留意 / 红需关注），
 * 无告警时用在线绿。零打断，看头像即知状态。
 */
export function StatusPetAvatar({ emoji, size = 64 }: { emoji: string; size?: number }) {
  const theme = useTheme();
  const { activeAlerts } = useAlerts();
  const worst = worstSeverity(activeAlerts);
  const ringColor =
    worst === 'critical'
      ? theme.danger
      : worst === 'warning'
        ? theme.warning
        : worst === 'info'
          ? theme.info
          : theme.online;

  return (
    <View
      style={[
        styles.avatar,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: theme.tintBackground,
          borderColor: ringColor,
          borderWidth: Math.max(2, size / 22),
        },
      ]}
    >
      <ThemedText style={{ fontSize: size * 0.46 }}>{emoji}</ThemedText>
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: {
    alignItems: 'center',
    justifyContent: 'center',
  },
});
