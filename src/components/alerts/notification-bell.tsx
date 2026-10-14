import { SymbolView } from 'expo-symbols';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { NotificationCenter } from '@/components/alerts/notification-center';
import { useTheme } from '@/hooks/use-theme';
import { useAlerts } from '@/hooks/use-alerts';

/** 顶部通知铃铛：未处理告警数角标，点开通知中心抽屉。 */
export function NotificationBell() {
  const theme = useTheme();
  const { activeAlerts, acknowledge } = useAlerts();
  const [open, setOpen] = useState(false);
  const unacknowledged = activeAlerts.filter((alert) => !alert.acknowledged).length;

  return (
    <>
      <Pressable onPress={() => setOpen(true)} hitSlop={10} accessibilityLabel="通知中心">
        <SymbolView
          name={{ ios: 'bell.fill', android: 'notifications', web: 'notifications' }}
          size={21}
          tintColor={theme.text}
        />
        {unacknowledged > 0 && (
          <View style={[styles.badge, { backgroundColor: theme.danger }]}>
            <Text style={styles.badgeText}>{Math.min(unacknowledged, 9)}</Text>
          </View>
        )}
      </Pressable>
      <NotificationCenter
        visible={open}
        alerts={activeAlerts}
        onClose={() => setOpen(false)}
        onAcknowledge={acknowledge}
      />
    </>
  );
}

const styles = StyleSheet.create({
  badge: {
    position: 'absolute',
    top: -4,
    right: -6,
    minWidth: 15,
    height: 15,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  badgeText: {
    color: '#ffffff',
    fontSize: 10,
    fontWeight: '700',
    lineHeight: 12,
  },
});
