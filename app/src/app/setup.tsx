import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { claimDevice, createPet, fetchMyDevice, fetchPets, type MyDevice } from '@/services/api';
import { reconnectTelemetry } from '@/services/telemetry-service';
import type { SocialPet } from '@/types/social';

type SetupPet = Pick<SocialPet, 'id' | 'name' | 'avatar_emoji'>;
const SPECIES = [
  { id: 'dog', label: '狗狗', emoji: '🐶' },
  { id: 'cat', label: '猫咪', emoji: '🐱' },
  { id: 'rabbit', label: '兔子', emoji: '🐰' },
] as const;
const DEVICE_ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

/** 一份已保存的档案，只进入一次创建请求；重试和重新进入只继续设备绑定。 */
export default function SetupScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [pet, setPet] = useState<SetupPet | null>(null);
  const [device, setDevice] = useState<MyDevice | null>(null);
  const [step, setStep] = useState<'pet' | 'device' | 'done'>('pet');
  const [name, setName] = useState('');
  const [species, setSpecies] = useState('dog');
  const [breed, setBreed] = useState('');
  const [emoji, setEmoji] = useState('🐶');
  const [deviceId, setDeviceId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    Promise.all([fetchPets(), fetchMyDevice()])
      .then(([petData, deviceData]) => {
        if (cancelled) return;
        const existing = petData.pets[0] ?? null;
        setPet(existing);
        setDevice(deviceData.device);
        setDeviceId(deviceData.device?.deviceId ?? '');
        setStep(
          !existing
            ? 'pet'
            : existing.device_id === deviceData.device?.deviceId &&
                deviceData.device?.petId === existing.id
              ? 'done'
              : 'device',
        );
        setError('');
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) setError('暂时无法读取档案，请检查网络后重试');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user, attempt]);

  const savePet = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await createPet({
        name: name.trim(),
        species,
        breed: breed.trim(),
        avatarEmoji: emoji,
        deviceId: device?.deviceId ?? '',
      });
      setPet({ id: result.pet.id, name: result.pet.name, avatar_emoji: emoji });
      // 先前已认领的设备在建档时一并关联，无需再次输入编码。
      if (device) {
        setDevice({ ...device, petId: result.pet.id, petName: result.pet.name });
        reconnectTelemetry();
        setStep('done');
      } else setStep('device');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '档案保存失败，请重试');
      // 响应丢失时也允许从服务器恢复已创建的档案，避免陷入重复创建。
      try {
        const data = await fetchPets();
        if (data.pets[0]) {
          setPet(data.pets[0]);
          setStep('device');
          setError('');
        }
      } catch {
        /* 保留表单及原始错误 */
      }
    } finally {
      setBusy(false);
    }
  };

  const pair = async () => {
    if (busy) return;
    if (!DEVICE_ID_PATTERN.test(deviceId.trim())) {
      setError('请填写完整设备编码，只能包含字母、数字、下划线和连字符');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await claimDevice(deviceId.trim());
      if (result.device.petId !== pet?.id) {
        setError('设备已认领，但还未关联档案。请稍后重新进入设置重试');
        return;
      }
      setDevice(result.device);
      reconnectTelemetry();
      setStep('done');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '绑定失败，请检查网络后重试');
    } finally {
      setBusy(false);
    }
  };

  const home = () => router.dismissTo('/');
  const stepIndex = step === 'pet' ? 0 : step === 'device' ? 1 : 2;
  return (
    <KeyboardAvoidingView
      style={[styles.screen, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={[styles.header, { paddingTop: insets.top + Spacing.two }]}>
        <ThemedText type="subtitle">开启健康记录</ThemedText>
        <Pressable onPress={home} disabled={busy} hitSlop={12} accessibilityLabel="关闭设置">
          <SymbolView
            name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
            size={28}
            tintColor={theme.textSecondary}
          />
        </Pressable>
      </View>
      <ScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + Spacing.four }]}
      >
        {!user ? (
          authLoading ? (
            <ActivityIndicator color={theme.tint} />
          ) : (
            <>
              <ThemedText type="subtitle">先登录，保存你的宠物档案</ThemedText>
              <Pressable
                onPress={() => router.push('/login')}
                style={[styles.button, { backgroundColor: theme.tint }]}
              >
                <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                  去登录
                </ThemedText>
              </Pressable>
            </>
          )
        ) : loading ? (
          <ActivityIndicator color={theme.tint} />
        ) : !ready ? (
          <View style={styles.tip}>
            <ThemedText type="small" style={{ color: theme.danger }}>
              {error}
            </ThemedText>
            <Pressable
              onPress={() => {
                setLoading(true);
                setAttempt((n) => n + 1);
              }}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                重新加载
              </ThemedText>
            </Pressable>
          </View>
        ) : (
          <>
            <View style={styles.steps}>
              {['宠物档案', '绑定项圈', '开始记录'].map((label, index) => (
                <View key={label} style={styles.step}>
                  <View
                    style={[
                      styles.number,
                      {
                        backgroundColor: index <= stepIndex ? theme.tint : theme.backgroundSelected,
                      },
                    ]}
                  >
                    <ThemedText
                      type="smallBold"
                      style={{
                        color: index <= stepIndex ? theme.backgroundElement : theme.textSecondary,
                      }}
                    >
                      {index < stepIndex ? '✓' : index + 1}
                    </ThemedText>
                  </View>
                  <ThemedText
                    type="small"
                    style={{ color: index === stepIndex ? theme.tint : theme.textSecondary }}
                  >
                    {label}
                  </ThemedText>
                </View>
              ))}
            </View>
            {step === 'pet' ? (
              <>
                <ThemedText type="subtitle">先认识一下你的毛孩子</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  起个名字就能建立档案，下一步再绑定项圈。
                </ThemedText>
                <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText style={styles.emoji}>{emoji}</ThemedText>
                </View>
                <ThemedText type="smallBold">宠物名字</ThemedText>
                <TextInput
                  value={name}
                  onChangeText={setName}
                  placeholder="它叫什么名字？"
                  accessibilityLabel="宠物名字"
                  placeholderTextColor={theme.textSecondary}
                  maxLength={20}
                  editable={!busy}
                  style={[
                    styles.input,
                    { color: theme.text, borderColor: theme.backgroundSelected },
                  ]}
                />
                <View style={styles.steps}>
                  {SPECIES.map((item) => (
                    <Pressable
                      key={item.id}
                      disabled={busy}
                      onPress={() => {
                        setSpecies(item.id);
                        setEmoji(item.emoji);
                      }}
                      style={[
                        styles.choice,
                        {
                          backgroundColor:
                            species === item.id ? theme.tintBackground : theme.backgroundElement,
                          borderColor: species === item.id ? theme.tint : theme.backgroundSelected,
                        },
                      ]}
                    >
                      <ThemedText type="smallBold">
                        {item.emoji} {item.label}
                      </ThemedText>
                    </Pressable>
                  ))}
                </View>
                <ThemedText type="smallBold">品种 · 选填</ThemedText>
                <TextInput
                  value={breed}
                  onChangeText={setBreed}
                  placeholder="不知道也可以不填"
                  maxLength={40}
                  editable={!busy}
                  placeholderTextColor={theme.textSecondary}
                  style={[
                    styles.input,
                    { color: theme.text, borderColor: theme.backgroundSelected },
                  ]}
                />
              </>
            ) : step === 'device' ? (
              <>
                <ThemedText type="subtitle">为{pet?.name}绑定项圈</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  档案已保存。绑定后，首页会显示这只项圈的真实数据。
                </ThemedText>
                <View style={[styles.tip, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText type="smallBold">① 给项圈开机，确认已联网</ThemedText>
                  <ThemedText type="smallBold">② 输入机身或包装上的完整编码</ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    通过设备编码绑定，无需在手机蓝牙设置里配对。
                  </ThemedText>
                </View>
                <ThemedText type="smallBold">设备编码</ThemedText>
                <TextInput
                  value={deviceId}
                  onChangeText={setDeviceId}
                  placeholder="petsense-esp32-001"
                  accessibilityLabel="设备编码"
                  placeholderTextColor={theme.textSecondary}
                  autoCapitalize="none"
                  autoCorrect={false}
                  maxLength={40}
                  editable={!busy}
                  style={[
                    styles.input,
                    { color: theme.text, borderColor: theme.backgroundSelected },
                  ]}
                />
                <ThemedText type="small" themeColor="textSecondary">
                  请完整输入编码，包含其中的连字符。
                </ThemedText>
              </>
            ) : (
              <>
                <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText style={styles.emoji}>✓</ThemedText>
                </View>
                <ThemedText type="subtitle">{pet?.name}的项圈已绑定</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {device?.deviceId}
                </ThemedText>
                <View style={[styles.tip, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText type="smallBold">接下来，给它戴好项圈</ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    保持项圈开机并联网，确认传感器贴合。收到有效采样后，首页会自动显示心率、血氧与体温。
                  </ThemedText>
                </View>
              </>
            )}
            {error ? (
              <View accessibilityLiveRegion="polite" style={styles.tip}>
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {error}
                </ThemedText>
              </View>
            ) : null}
            <Pressable
              onPress={step === 'pet' ? savePet : step === 'device' ? pair : home}
              disabled={
                busy || (step === 'pet' && !name.trim()) || (step === 'device' && !deviceId.trim())
              }
              style={[
                styles.button,
                {
                  backgroundColor: theme.tint,
                  opacity:
                    busy ||
                    (step === 'pet' && !name.trim()) ||
                    (step === 'device' && !deviceId.trim())
                      ? 0.5
                      : 1,
                },
              ]}
            >
              {busy ? (
                <ActivityIndicator color={theme.backgroundElement} />
              ) : (
                <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                  {step === 'pet'
                    ? '保存档案，下一步'
                    : step === 'device'
                      ? '绑定这只项圈'
                      : '去首页查看'}
                </ThemedText>
              )}
            </Pressable>
            {step === 'device' && (
              <Pressable disabled={busy} onPress={home} style={styles.skip}>
                <ThemedText type="small" themeColor="textSecondary">
                  稍后绑定，先看看首页
                </ThemedText>
              </Pressable>
            )}
          </>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.three,
  },
  content: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    padding: Spacing.four,
    gap: Spacing.three,
  },
  steps: { flexDirection: 'row', gap: Spacing.two, marginBottom: Spacing.two },
  step: { flex: 1, alignItems: 'center', gap: Spacing.two },
  number: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatar: {
    width: 86,
    height: 86,
    borderRadius: 43,
    alignSelf: 'center',
    alignItems: 'center',
    justifyContent: 'center',
  },
  emoji: { fontSize: 42, lineHeight: 52 },
  input: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 14,
    fontSize: 16,
    width: '100%',
  },
  choice: { flex: 1, borderWidth: 1, borderRadius: 14, paddingVertical: 12, alignItems: 'center' },
  tip: { borderRadius: 16, padding: Spacing.three, gap: Spacing.two },
  button: {
    minHeight: 50,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.three,
  },
  skip: { alignItems: 'center', padding: Spacing.two },
});
