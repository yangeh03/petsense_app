import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { notifyPetChanged } from '@/services/pet-profile-events';
import { fetchMyDevice, releaseDevice, type MyDevice } from '@/services/api';
import { reconnectTelemetry } from '@/services/telemetry-service';

/** 设备管理只负责查看和解绑；建档与绑定统一进入可续接的 setup 流程。 */
export function DeviceCard() {
  const theme = useTheme();
  const router = useRouter();
  const { user } = useAuth();
  const [stored, setStored] = useState<{ userId: number; device: MyDevice | null } | null>(null);
  const device = stored?.userId === user?.id ? stored?.device : null;
  const sequence = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) return;
    try {
      const data = await fetchMyDevice();
      if (current !== sequence.current) return;
      setStored({ userId: user.id, device: data.device });
      setError('');
    } catch {
      if (current === sequence.current) setError('设备信息加载失败，请重试');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [user]);
  usePetChanges(refresh);
  useFocusEffect(
    useCallback(() => {
      void refresh();
      return () => {
        sequence.current++;
      };
    }, [refresh]),
  );

  const confirmRelease = () => {
    if (!device || !user) return;
    Alert.alert('解绑项圈', '解绑后将停止接收这只项圈的数据，宠物档案会保留。', [
      { text: '取消', style: 'cancel' },
      {
        text: '解绑',
        style: 'destructive',
        onPress: () => {
          void releaseDevice(device.deviceId)
            .then(() => {
              setStored({ userId: user.id, device: null });
              notifyPetChanged(user.id);
              reconnectTelemetry();
            })
            .catch(() => Alert.alert('解绑失败', '请稍后重试'));
        },
      },
    ]);
  };
  return (
    <ThemedView type="backgroundElement" style={styles.card}>
      <ThemedText type="smallBold">我的项圈</ThemedText>
      {loading ? (
        <ActivityIndicator color={theme.tint} />
      ) : error ? (
        <Pressable onPress={() => void refresh()}>
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        </Pressable>
      ) : device ? (
        <>
          <ThemedText type="smallBold">
            {device.online ? '● 在线' : '○ 暂未在线'}
            {device.batteryPercent !== null ? ` · 电量 ${device.batteryPercent}%` : ''}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {device.deviceId}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {device.petName ? `正在守护 ${device.petName}` : '已认领，继续建立宠物档案完成关联'}
          </ThemedText>
          <View style={styles.actions}>
            {!device.petId && (
              <Pressable onPress={() => router.push('/setup')}>
                <ThemedText type="smallBold" style={{ color: theme.tint }}>
                  继续设置
                </ThemedText>
              </Pressable>
            )}
            <Pressable onPress={confirmRelease} hitSlop={8}>
              <ThemedText type="small" themeColor="textSecondary">
                解绑项圈
              </ThemedText>
            </Pressable>
          </View>
        </>
      ) : (
        <>
          <ThemedText type="small" themeColor="textSecondary">
            建立宠物档案，再输入项圈编码。完成后可在首页查看数据。
          </ThemedText>
          <Pressable
            onPress={() => router.push('/setup')}
            style={[styles.button, { backgroundColor: theme.tintBackground }]}
          >
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              设置宠物与项圈
            </ThemedText>
          </Pressable>
        </>
      )}
    </ThemedView>
  );
}
const styles = StyleSheet.create({
  card: { borderRadius: 18, padding: Spacing.three, gap: Spacing.two },
  actions: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: Spacing.two },
  button: { padding: Spacing.three, borderRadius: 12, alignItems: 'center' },
});
