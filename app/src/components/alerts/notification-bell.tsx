import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { NotificationCenter } from '@/components/alerts/notification-center';
import { useTheme } from '@/hooks/use-theme';
import { useAgentInbox } from '@/hooks/use-agent-inbox';
import { useAlerts } from '@/hooks/use-alerts';

/**
 * 顶部通知铃铛：未处理通知数角标，点开通知中心抽屉。
 *
 * W12：服务端接管后角标用服务端未读数（与今日预览同一语义）；未接管的本地告警
 * 仍由本机规则产生并保留在抽屉里（capability 未确认时角标回落到本地计数）。
 */
export function NotificationBell() {
  const theme = useTheme();
  const router = useRouter();
  const { activeAlerts, acknowledge } = useAlerts();
  const { items, unreadCount, capability } = useAgentInbox();
  const [open, setOpen] = useState(false);
  const localUnacknowledged = activeAlerts.filter((alert) => !alert.acknowledged).length;
  const serverInbox = capability?.serverInbox === true;
  const badgeCount = serverInbox ? unreadCount : localUnacknowledged;

  return (
    <>
      <Pressable onPress={() => setOpen(true)} hitSlop={10} accessibilityLabel="通知中心">
        <SymbolView
          name={{ ios: 'bell.fill', android: 'notifications', web: 'notifications' }}
          size={21}
          tintColor={theme.text}
        />
        {badgeCount > 0 && (
          <View style={[styles.badge, { backgroundColor: theme.danger }]}>
            <Text style={styles.badgeText}>{Math.min(badgeCount, 9)}</Text>
          </View>
        )}
      </Pressable>
      <NotificationCenter
        visible={open}
        alerts={activeAlerts}
        inboxItems={items}
        serverInbox={serverInbox}
        onClose={() => setOpen(false)}
        onAcknowledge={acknowledge}
        onOpenInbox={(id) => {
          setOpen(false);
          router.push(`/inbox/${id}`);
        }}
        onOpenInboxList={() => {
          setOpen(false);
          router.push('/agent-inbox');
        }}
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
