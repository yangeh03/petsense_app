import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { UwbSetup } from '@/components/location/uwb-setup';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useObservations } from '@/hooks/use-observations';
import { useTheme } from '@/hooks/use-theme';

/**
 * 室内地图标定的专属页面：三步流程由服务端会话承载，离开页面不会丢进度。
 * 发布成功后回到位置页即可看到新地图（位置页在聚焦时会重新拉取）。
 */
export default function UwbSetupScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { dashboard } = useObservations('day');
  const deviceId = dashboard?.pet?.device_id;
  const [published, setPublished] = useState(false);
  const [dragging, setDragging] = useState(false);
  return (
    <ScrollView
      scrollEnabled={!dragging}
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.scroll}
    >
      <View style={[styles.container, { paddingBottom: insets.bottom + Spacing.five }]}>
        {!deviceId && (
          <ThemedText themeColor="textSecondary" style={styles.hint}>
            还没有绑定项圈。先完成设备绑定，再回到这里标定室内地图。
          </ThemedText>
        )}
        {deviceId && (
          <UwbSetup
            key={deviceId}
            deviceId={deviceId}
            onPublished={() => setPublished(true)}
            onDragging={setDragging}
          />
        )}
        {published && (
          <Pressable
            accessibilityRole="button"
            onPress={() => router.back()}
            style={[styles.backButton, { backgroundColor: theme.tintBackground }]}
          >
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              返回位置页查看新地图 ›
            </ThemedText>
          </Pressable>
        )}
      </View>
    </ScrollView>
  );
}
const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.four,
    gap: Spacing.four,
  },
  hint: { lineHeight: 24 },
  backButton: { padding: Spacing.three, borderRadius: 12, alignItems: 'center' },
});
