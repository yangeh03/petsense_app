/**
 * 设备卡：我的项圈状态（在线/电量/绑定宠物）+ 配对弹层 + 解绑。
 * 无设备时显示虚线占位卡引导绑定；配对 = 输入机身设备 ID 认领。
 */

import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useFocusEffect } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { useTheme } from '@/hooks/use-theme';
import { ApiError, claimDevice, fetchMyDevice, releaseDevice, type MyDevice } from '@/services/api';

const DEVICE_ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

export function DeviceCard() {
  const theme = useTheme();
  const [device, setDevice] = useState<MyDevice | null>(null);
  const [loading, setLoading] = useState(true);
  const [pairing, setPairing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchMyDevice();
      setDevice(data.device);
    } catch {
      // 拉取失败保留旧数据
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const confirmRelease = () => {
    if (!device) return;
    Alert.alert('解绑设备', '解绑后设备数据不再归属本账号，其他账号可认领。确定解绑？', [
      { text: '取消', style: 'cancel' },
      {
        text: '解绑',
        style: 'destructive',
        onPress: () => {
          void releaseDevice(device.deviceId)
            .then(() => setDevice(null))
            .catch(() => Alert.alert('解绑失败', '请稍后重试'));
        },
      },
    ]);
  };

  if (loading) {
    return (
      <ThemedView type="backgroundElement" style={styles.card}>
        <ActivityIndicator color={theme.textSecondary} />
      </ThemedView>
    );
  }

  if (!device) {
    return (
      <>
        <Pressable onPress={() => setPairing(true)}>
          <ThemedView
            type="backgroundElement"
            style={[styles.card, styles.empty, { borderColor: theme.tint }]}
          >
            <ThemedText type="smallBold">绑定你的项圈 🐾</ThemedText>
            <ThemedText themeColor="textSecondary" type="small">
              输入机身标签上的设备 ID，绑定后实时数据只归属本账号
            </ThemedText>
          </ThemedView>
        </Pressable>
        <PairDeviceModal
          visible={pairing}
          onClose={() => setPairing(false)}
          onClaimed={(claimed) => {
            setDevice(claimed);
            setPairing(false);
          }}
        />
      </>
    );
  }

  return (
    <ThemedView type="backgroundElement" style={styles.card}>
      <View style={styles.row}>
        <View style={styles.dotWrap}>
          <View
            style={[
              styles.dot,
              { backgroundColor: device.online ? theme.online : theme.textSecondary },
            ]}
          />
          <ThemedText type="smallBold">
            {device.name || device.deviceId}
            {device.online ? '' : ' · 离线'}
          </ThemedText>
        </View>
        {device.batteryPercent !== null && (
          <ThemedText themeColor="textSecondary" type="small">
            🔋 {device.batteryPercent}%
          </ThemedText>
        )}
      </View>
      <ThemedText themeColor="textSecondary" type="small">
        设备 ID：{device.deviceId}
        {device.petName ? ` · 绑定 ${device.petName}` : ' · 未绑定宠物'}
      </ThemedText>
      <Pressable onPress={confirmRelease} hitSlop={8} style={styles.release}>
        <Text style={{ color: theme.danger, fontSize: 12 }}>解绑设备</Text>
      </Pressable>
    </ThemedView>
  );
}

function PairDeviceModal({
  visible,
  onClose,
  onClaimed,
}: {
  visible: boolean;
  onClose: () => void;
  onClaimed: (device: MyDevice) => void;
}) {
  const theme = useTheme();
  const [deviceId, setDeviceId] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const trimmed = deviceId.trim();
    if (!DEVICE_ID_PATTERN.test(trimmed)) {
      setError('设备 ID 需为 1—40 位字母、数字、下划线或连字符');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const data = await claimDevice(trimmed);
      onClaimed(data.device);
      setDeviceId('');
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError('该设备已被其他账号绑定，请先在原账号解绑');
      } else {
        setError(e instanceof Error ? e.message : '绑定失败，请稍后重试');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalWrap}>
        <ThemedView type="backgroundElement" style={styles.modal}>
          <ThemedText type="smallBold">绑定项圈</ThemedText>
          <TextInput
            value={deviceId}
            onChangeText={setDeviceId}
            placeholder="esp32-xxxx（机身标签可查）"
            placeholderTextColor={theme.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, { color: theme.text }]}
          />
          <ThemedText themeColor="textSecondary" type="small">
            设备 ID 印在项圈机身/包装标签上；不知道 ID 时可联系设备提供方查询
          </ThemedText>
          {error ? <Text style={{ color: theme.danger, fontSize: 12 }}>{error}</Text> : null}
          <View style={styles.modalActions}>
            <Pressable onPress={onClose} hitSlop={8}>
              <Text style={{ color: theme.textSecondary, fontSize: 14 }}>取消</Text>
            </Pressable>
            <Pressable onPress={() => void submit()} disabled={submitting} hitSlop={8}>
              <Text style={{ color: theme.tint, fontSize: 14, fontWeight: '600' }}>
                {submitting ? '绑定中…' : '绑定'}
              </Text>
            </Pressable>
          </View>
        </ThemedView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 14,
    borderWidth: 1,
    padding: 14,
    gap: 6,
    marginHorizontal: 16,
    marginTop: 12,
  },
  empty: {
    borderStyle: 'dashed',
    alignItems: 'center',
    paddingVertical: 18,
    gap: 4,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  dotWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  release: {
    alignSelf: 'flex-end',
    paddingVertical: 4,
  },
  modalWrap: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'center',
    padding: 24,
  },
  modal: {
    borderRadius: 16,
    padding: 18,
    gap: 10,
  },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  modalActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 22,
    marginTop: 4,
  },
});
