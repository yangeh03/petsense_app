import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { SymbolView } from 'expo-symbols';
import { Stack, useFocusEffect, useNavigation, useRouter } from 'expo-router';
import { usePreventRemove, type NavigationAction } from 'expo-router/react-navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PetProfileForm } from '@/components/pets/pet-profile-form';
import { AvatarTemplatePicker } from '@/components/pets/avatar-template-picker';
import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import {
  absoluteUrl,
  addWeightRecord,
  deleteWeightRecord,
  fetchPets,
  fetchWeightRecords,
  updatePet,
  type WeightRecord,
} from '@/services/api';
import {
  ageLabel,
  birthLabel,
  buildPetUpdate,
  createPetDraft,
  isPetDraftDirty,
  petSaveError,
  speciesLabel,
  validatePetDraft,
  type PetDraft,
} from '@/services/pet-profile';
import { notifyPetChanged } from '@/services/pet-profile-events';
import type { SocialPet } from '@/types/social';

export default function PetScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const navigation = useNavigation();
  const { user, loading: authLoading } = useAuth();
  const [resource, setResource] = useState<{ userId: number; pet: SocialPet | null } | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [original, setOriginal] = useState<SocialPet | null>(null);
  const [draft, setDraft] = useState<PetDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<NavigationAction | null>(null);
  // W03 体重记录（编辑态）：按日追加、可删；同日再记覆盖当日（agent-w03-spec.md §4.3）
  const [weightRecords, setWeightRecords] = useState<WeightRecord[]>([]);
  const [weightKg, setWeightKg] = useState('');
  const [weightDate, setWeightDate] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
      now.getDate(),
    ).padStart(2, '0')}`;
  });
  const [weightBusy, setWeightBusy] = useState(false);
  const [weightError, setWeightError] = useState('');
  const sequence = useRef(0);
  const savingRequest = useRef(false);
  const pet = resource?.userId === user?.id ? resource?.pet : null;
  const editing = !!draft && original?.user_id === user?.id;
  const dirty = !!(editing && original && draft && isPetDraftDirty(original, draft));
  const errors = draft ? validatePetDraft(draft) : {};

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) return;
    try {
      const result = await fetchPets();
      if (current !== sequence.current) return;
      setResource({ userId: user.id, pet: result.pets[0] ?? null });
      setLoadError('');
    } catch {
      if (current === sequence.current) setLoadError('宠物资料加载失败，请检查网络后重试');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [user]);
  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        sequence.current++;
      };
    }, [load]),
  );
  usePetChanges(load);

  usePreventRemove(!!user && (dirty || saving), ({ data }) => {
    if (savingRequest.current) return;
    setPendingAction(data.action);
    setDiscardOpen(true);
  });
  useEffect(() => {
    if (Platform.OS !== 'web' || (!dirty && !saving)) return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, saving]);

  const back = () => (router.canGoBack() ? router.back() : router.replace('/profile'));
  const stopEditing = () => {
    if (dirty) {
      setPendingAction(null);
      setDiscardOpen(true);
    } else {
      setDraft(null);
      setOriginal(null);
      setError('');
    }
  };
  const loadWeights = useCallback(async (petId: number) => {
    try {
      const data = await fetchWeightRecords(petId);
      setWeightRecords(data.records);
    } catch {
      setWeightRecords([]);
    }
  }, []);

  const addWeight = async () => {
    if (!pet || weightBusy) return;
    const kg = Number(weightKg.trim());
    if (!Number.isFinite(kg) || kg < 0.1 || kg > 100) {
      setWeightError('体重请输入 0.1—100 之间的公斤数');
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(weightDate.trim())) {
      setWeightError('日期格式：2026-11-22');
      return;
    }
    setWeightBusy(true);
    setWeightError('');
    try {
      await addWeightRecord(pet.id, { weightKg: kg, measuredOn: weightDate.trim() });
      setWeightKg('');
      await loadWeights(pet.id);
      await load(); // 刷新 pet.latestWeight
    } catch (exc) {
      setWeightError(exc instanceof Error ? exc.message : '保存失败，请稍后再试');
    } finally {
      setWeightBusy(false);
    }
  };

  const removeWeight = async (recordId: number) => {
    if (!pet || weightBusy) return;
    setWeightBusy(true);
    setWeightError('');
    try {
      await deleteWeightRecord(pet.id, recordId);
      await loadWeights(pet.id);
      await load();
    } catch (exc) {
      setWeightError(exc instanceof Error ? exc.message : '删除失败，请稍后再试');
    } finally {
      setWeightBusy(false);
    }
  };

  const pickPhoto = async () => {
    setPicking(true);
    setError('');
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.8,
      });
      if (result.canceled) return;
      const asset = result.assets?.[0];
      if (!asset) return;
      if (asset.fileSize && asset.fileSize > 10 * 1024 * 1024) {
        setError('图片超过 10MB，请选择较小的照片');
        return;
      }
      setDraft((current) =>
        current
          ? {
              ...current,
              avatarMode: 'photo',
              photo: {
                uri: asset.uri,
                name: asset.fileName ?? 'pet.jpg',
                type: asset.mimeType ?? 'image/jpeg',
              },
            }
          : null,
      );
    } catch {
      setError('无法打开相册，请检查照片访问权限后重试');
    } finally {
      setPicking(false);
    }
  };
  const save = async () => {
    if (!user || !pet || !draft || !dirty || Object.keys(errors).length || savingRequest.current)
      return;
    savingRequest.current = true;
    const current = ++sequence.current;
    setSaving(true);
    setError('');
    try {
      const result = await updatePet(pet.id, buildPetUpdate(draft));
      if (current !== sequence.current) return;
      setResource({ userId: user.id, pet: result.pet });
      setDraft(null);
      setOriginal(null);
      setSuccess('宠物资料已保存');
      notifyPetChanged(user.id);
    } catch (exc) {
      if (current === sequence.current) setError(petSaveError(exc));
    } finally {
      savingRequest.current = false;
      setSaving(false);
    }
  };

  const label = resource?.userId === user?.id;
  return (
    <KeyboardAvoidingView
      style={[styles.screen, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <Stack.Screen options={{ gestureEnabled: !editing && !saving }} />
      <View style={[styles.header, { paddingTop: insets.top + Spacing.two }]}>
        <Pressable
          onPress={back}
          disabled={saving || picking}
          accessibilityRole="button"
          accessibilityLabel="返回"
          style={styles.headerAction}
        >
          <SymbolView
            name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
            size={22}
            tintColor={theme.text}
          />
        </Pressable>
        <ThemedText type="smallBold" style={styles.title}>
          {editing ? '编辑宠物资料' : '宠物档案'}
        </ThemedText>
        {pet && !editing ? (
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              setOriginal(pet);
              setDraft(createPetDraft(pet));
              setSuccess('');
              setError('');
              void loadWeights(pet.id);
            }}
            style={styles.headerAction}
          >
            <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
              编辑
            </ThemedText>
          </Pressable>
        ) : (
          <View style={styles.headerAction} />
        )}
      </View>
      <ScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + Spacing.five }]}
      >
        {authLoading || (user && loading && !label) ? (
          <ActivityIndicator color={theme.tint} style={styles.loader} />
        ) : !user ? (
          <View style={styles.empty}>
            <ThemedText type="subtitle">登录后管理宠物档案</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              名字、照片和项圈资料会保存在你的账号中。
            </ThemedText>
            <Pressable
              onPress={() => router.push('/login')}
              style={[styles.primary, { backgroundColor: theme.tint }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.onTint }}>
                去登录
              </ThemedText>
            </Pressable>
          </View>
        ) : (
          <>
            {!!loadError && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {loadError}
                </ThemedText>
                <Pressable onPress={() => void load()} accessibilityRole="button">
                  <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                    重新加载
                  </ThemedText>
                </Pressable>
              </View>
            )}
            {pet ? (
              editing && draft && original ? (
                <>
                  <ThemedText type="small" themeColor="textSecondary">
                    让每个页面都认识同一个它。修改只会在保存后生效。
                  </ThemedText>
                  <PetProfileForm
                    pet={original}
                    draft={draft}
                    errors={errors}
                    disabled={saving}
                    picking={picking}
                    onChange={(patch) => {
                      setDraft((current) => (current ? { ...current, ...patch } : null));
                      setError('');
                    }}
                    onPickPhoto={() => void pickPhoto()}
                  />
                  <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                    <ThemedText type="smallBold">体重记录</ThemedText>
                    <View style={styles.weightInputs}>
                      <TextInput
                        value={weightKg}
                        onChangeText={setWeightKg}
                        editable={!weightBusy}
                        accessibilityLabel="体重公斤数"
                        placeholder="公斤，如 4.5"
                        placeholderTextColor={theme.textSecondary}
                        keyboardType="decimal-pad"
                        returnKeyType="done"
                        style={[
                          styles.weightInput,
                          {
                            color: theme.text,
                            backgroundColor: theme.background,
                            borderColor: theme.backgroundSelected,
                          },
                        ]}
                      />
                      <TextInput
                        value={weightDate}
                        onChangeText={setWeightDate}
                        editable={!weightBusy}
                        accessibilityLabel="称重日期"
                        placeholder="2026-11-22"
                        placeholderTextColor={theme.textSecondary}
                        returnKeyType="done"
                        style={[
                          styles.weightInput,
                          styles.weightDateInput,
                          {
                            color: theme.text,
                            backgroundColor: theme.background,
                            borderColor: theme.backgroundSelected,
                          },
                        ]}
                      />
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel="添加体重记录"
                        disabled={weightBusy || !weightKg.trim()}
                        onPress={() => void addWeight()}
                        style={[
                          styles.weightAdd,
                          {
                            backgroundColor:
                              weightBusy || !weightKg.trim()
                                ? theme.backgroundSelected
                                : theme.tint,
                          },
                        ]}
                      >
                        {weightBusy ? (
                          <ActivityIndicator size="small" color={theme.onTint} />
                        ) : (
                          <ThemedText type="smallBold" style={{ color: theme.onTint }}>
                            记一笔
                          </ThemedText>
                        )}
                      </Pressable>
                    </View>
                    {!!weightError && (
                      <ThemedText type="small" style={{ color: theme.danger }}>
                        {weightError}
                      </ThemedText>
                    )}
                    {weightRecords.length ? (
                      weightRecords.slice(0, 10).map((record) => (
                        <View key={record.id} style={styles.detail}>
                          <ThemedText type="small" style={{ flex: 1 }}>
                            {record.measuredOn} · {record.weightKg} kg
                            {record.rawText ? `（${record.rawText}）` : ''}
                          </ThemedText>
                          <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={`删除 ${record.measuredOn} 的体重记录`}
                            disabled={weightBusy}
                            onPress={() => void removeWeight(record.id)}
                            style={styles.weightDelete}
                          >
                            <ThemedText type="small" style={{ color: theme.danger }}>
                              删除
                            </ThemedText>
                          </Pressable>
                        </View>
                      ))
                    ) : (
                      <ThemedText type="small" themeColor="textSecondary">
                        还没有体重记录；同一天再记会覆盖当天的数值。
                      </ThemedText>
                    )}
                  </View>
                  {draft.species === 'dog' && (
                    <AvatarTemplatePicker
                      selectedId={draft.avatarTemplateId}
                      disabled={saving}
                      onChange={(avatarTemplateId) =>
                        setDraft((current) => (current ? { ...current, avatarTemplateId } : null))
                      }
                    />
                  )}
                  {!!error && (
                    <ThemedText
                      accessibilityRole="alert"
                      type="small"
                      style={{ color: theme.danger }}
                    >
                      {error}。修改已保留，可重试保存。
                    </ThemedText>
                  )}
                  <Pressable
                    onPress={() => void save()}
                    disabled={saving || picking || !dirty || !!Object.keys(errors).length}
                    accessibilityRole="button"
                    style={[
                      styles.primary,
                      {
                        backgroundColor: theme.tint,
                        opacity:
                          saving || picking || !dirty || !!Object.keys(errors).length ? 0.5 : 1,
                      },
                    ]}
                  >
                    {saving ? (
                      <ActivityIndicator color={theme.onTint} />
                    ) : (
                      <ThemedText type="smallBold" style={{ color: theme.onTint }}>
                        保存修改
                      </ThemedText>
                    )}
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    disabled={saving || picking}
                    onPress={stopEditing}
                    style={styles.primary}
                  >
                    <ThemedText type="small" themeColor="textSecondary">
                      取消编辑
                    </ThemedText>
                  </Pressable>
                </>
              ) : (
                <>
                  {!!success && (
                    <ThemedText
                      accessibilityLiveRegion="polite"
                      type="smallBold"
                      style={{ color: theme.onlineForeground }}
                    >
                      {success}
                    </ThemedText>
                  )}
                  <View style={[styles.hero, { backgroundColor: theme.tintBackground }]}>
                    <View style={[styles.avatar, { backgroundColor: theme.backgroundElement }]}>
                      {pet.avatarImageUrl ? (
                        <Image
                          source={{ uri: absoluteUrl(pet.avatarImageUrl) }}
                          style={styles.photo}
                          contentFit="cover"
                        />
                      ) : (
                        <ThemedText style={styles.emoji}>{pet.avatar_emoji}</ThemedText>
                      )}
                    </View>
                    <ThemedText type="subtitle" style={styles.petName}>
                      {pet.name}
                    </ThemedText>
                    <ThemedText type="small" themeColor="textSecondary">
                      {speciesLabel(pet.species)} · {pet.breed || '品种未填写'}
                    </ThemedText>
                  </View>
                  <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                    <ThemedText type="smallBold">基本资料</ThemedText>
                    <Detail label="名字" value={pet.name} />
                    <Detail label="物种" value={speciesLabel(pet.species)} />
                    <Detail label="品种" value={pet.breed || '未填写'} />
                    <Detail
                      label="生日"
                      value={
                        pet.birthDate
                          ? `${birthLabel(pet)}${ageLabel(pet) ? ` · ${ageLabel(pet)}` : ''}`
                          : '未填写'
                      }
                    />
                    <Detail
                      label="体重"
                      value={
                        pet.latestWeight
                          ? `${pet.latestWeight.weightKg} kg · ${pet.latestWeight.measuredOn}`
                          : '未记录'
                      }
                    />
                  </View>
                  <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                    <ThemedText type="smallBold">守护它的项圈</ThemedText>
                    {pet.device_id ? (
                      <>
                        <Detail label="设备编码" value={pet.device_id} />
                        <Detail label="连接状态" value={pet.online ? '在线' : '暂未在线'} />
                        <Detail
                          label="电量"
                          value={pet.batteryPercent != null ? `${pet.batteryPercent}%` : '暂无数据'}
                        />
                        <ThemedText type="small" themeColor="textSecondary">
                          修改资料会保留项圈绑定和历史记录。
                        </ThemedText>
                      </>
                    ) : (
                      <>
                        <ThemedText type="small" themeColor="textSecondary">
                          尚未绑定项圈，绑定后可接收真实健康数据。
                        </ThemedText>
                        <Pressable
                          accessibilityRole="button"
                          onPress={() => router.push('/setup')}
                          style={[styles.secondary, { backgroundColor: theme.tintBackground }]}
                        >
                          <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                            绑定项圈
                          </ThemedText>
                        </Pressable>
                      </>
                    )}
                  </View>
                </>
              )
            ) : (
              !loadError && (
                <View style={styles.empty}>
                  <ThemedText style={styles.emoji}>🐾</ThemedText>
                  <ThemedText type="subtitle">先认识你的毛孩子</ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    建立一份宠物档案，再绑定项圈。每个账号管理一只宠物。
                  </ThemedText>
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => router.push('/setup')}
                    style={[styles.primary, { backgroundColor: theme.tint }]}
                  >
                    <ThemedText type="smallBold" style={{ color: theme.onTint }}>
                      建立宠物档案
                    </ThemedText>
                  </Pressable>
                </View>
              )
            )}
          </>
        )}
      </ScrollView>
      <Modal
        visible={discardOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setDiscardOpen(false)}
      >
        <View style={[styles.backdrop, { backgroundColor: `${theme.text}66` }]}>
          <View
            accessibilityViewIsModal
            style={[styles.dialog, { backgroundColor: theme.backgroundElement }]}
          >
            <ThemedText type="subtitle">放弃未保存的修改？</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              名字、资料和头像修改尚未保存。
            </ThemedText>
            <Pressable
              accessibilityRole="button"
              onPress={() => setDiscardOpen(false)}
              style={[styles.primary, { backgroundColor: theme.tintBackground }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                继续编辑
              </ThemedText>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setDiscardOpen(false);
                setDraft(null);
                setOriginal(null);
                setError('');
                if (pendingAction) navigation.dispatch(pendingAction);
                setPendingAction(null);
              }}
              style={styles.primary}
            >
              <ThemedText type="smallBold" style={{ color: theme.danger }}>
                放弃修改
              </ThemedText>
            </Pressable>
          </View>
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detail}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText type="small" style={styles.detailValue}>
        {value}
      </ThemedText>
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.three,
    paddingBottom: Spacing.two,
  },
  headerAction: { minWidth: 52, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  title: { flex: 1, textAlign: 'center', fontSize: 18 },
  content: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    padding: Spacing.four,
    gap: Spacing.three,
  },
  loader: { marginVertical: Spacing.six },
  empty: { gap: Spacing.four, paddingVertical: Spacing.five },
  hero: { padding: Spacing.five, borderRadius: 28, alignItems: 'center', gap: Spacing.three },
  avatar: {
    width: 108,
    height: 108,
    borderRadius: 54,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  photo: { width: '100%', height: '100%' },
  emoji: { fontSize: 54, lineHeight: 70 },
  petName: { textAlign: 'center' },
  card: { padding: Spacing.four, borderRadius: 24, gap: Spacing.three },
  detail: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Spacing.three,
    justifyContent: 'space-between',
  },
  detailValue: { flex: 1, textAlign: 'right' },
  weightInputs: { flexDirection: 'row', gap: Spacing.two, alignItems: 'center' },
  weightInput: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 14,
    padding: Spacing.two,
    fontSize: 16,
    minHeight: 48,
  },
  weightDateInput: { flex: 1.2 },
  weightAdd: {
    minHeight: 48,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    alignItems: 'center',
    justifyContent: 'center',
  },
  weightDelete: { paddingVertical: 6, paddingLeft: Spacing.two },
  primary: {
    minHeight: 52,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.three,
  },
  secondary: {
    minHeight: 48,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.two,
  },
  backdrop: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: Spacing.four },
  dialog: {
    width: '100%',
    maxWidth: 420,
    padding: Spacing.four,
    borderRadius: 24,
    gap: Spacing.three,
  },
});
