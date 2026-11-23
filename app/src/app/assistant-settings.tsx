/**
 * W03 助手设定页（agent-w03-spec.md §4.4，modal）：四字段全直写（D3）。
 * 名字 / 称呼主人 / 语气详略 / 主动程度（W09 起对周期回顾与设备提醒生效）；保存后伙伴页即时生效
 * （companion store 同步刷新，页头副标题与消息标签更新）。
 * 入口：我的页「伙伴」分组行、伙伴页点击助手头像/名字（成员详情）。
 */

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
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { updateAssistantSettings, type CompanionSettings } from '@/services/api';
import { useCompanionSettingsStore } from '@/stores/companion-settings';

const VERBOSITY_OPTIONS: { value: CompanionSettings['verbosity']; label: string; hint: string }[] =
  [
    { value: 'concise', label: '简洁', hint: '默认简短回答，追问再展开' },
    { value: 'detailed', label: '详细', hint: '回答更完整，主动补充背景' },
  ];
const PROACTIVITY_OPTIONS: { value: 0 | 1 | 2; label: string; hint: string }[] = [
  { value: 0, label: '安静', hint: '尽量少打扰' },
  { value: 1, label: '标准', hint: '值得说才开口' },
  { value: 2, label: '多提醒', hint: '更主动地提醒与追问' },
];

export default function AssistantSettingsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { user } = useAuth();
  const settings = useCompanionSettingsStore((state) => state.settings);
  const loaded = useCompanionSettingsStore((state) => state.loaded);
  const refresh = useCompanionSettingsStore((state) => state.refresh);
  // 覆盖式草稿：未改动的字段跟随 store（登录/拉取后自动生效），改过才覆盖
  const [changes, setChanges] = useState<Partial<CompanionSettings> | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const draft: CompanionSettings = { ...settings, ...changes };
  const update = (patch: Partial<CompanionSettings>) => setChanges({ ...changes, ...patch });

  // 进入页面先拉取服务端当前值：store 可能还没加载（未经过伙伴页），
  // 不拉取会以默认值渲染表单，保存时把已持久化的设定覆盖回默认。
  useEffect(() => {
    if (user) void refresh();
  }, [user, refresh]);

  const dirty =
    changes != null &&
    Object.keys(changes).some(
      (key) => draft[key as keyof CompanionSettings] !== settings[key as keyof CompanionSettings],
    );
  const nameError = draft.assistantName.trim().length === 0 ? '名字需为 1—20 个字符' : '';

  const save = async () => {
    if (saving || nameError || !loaded) return;
    setSaving(true);
    setError('');
    try {
      await updateAssistantSettings({
        assistantName: draft.assistantName.trim(),
        ownerTitle: draft.ownerTitle?.trim() || '',
        verbosity: draft.verbosity,
        proactivity: draft.proactivity,
      });
      await refresh();
      setChanges(null); // 保存成功回到跟随 store
      setSaved(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存失败，请稍后再试');
    } finally {
      setSaving(false);
    }
  };

  const back = () => (router.canGoBack() ? router.back() : router.dismiss());
  const inputStyle = [
    styles.input,
    { color: theme.text, backgroundColor: theme.background, borderColor: theme.backgroundSelected },
  ];

  return (
    <KeyboardAvoidingView
      style={[styles.screen, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.header}>
        <Pressable
          onPress={back}
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
          助手设定
        </ThemedText>
        <View style={styles.headerAction} />
      </View>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}>
        {!user ? (
          <ThemedText type="small" themeColor="textSecondary">
            登录后可调整照料助手的设定。
          </ThemedText>
        ) : (
          <>
            {!!saved && (
              <ThemedText type="smallBold" style={{ color: theme.online }}>
                已保存，伙伴页立即生效。
              </ThemedText>
            )}
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">助手名字</ThemedText>
              <TextInput
                value={draft.assistantName}
                onChangeText={(assistantName) => update({ assistantName })}
                accessibilityLabel="助手名字"
                placeholder="照料助手"
                placeholderTextColor={theme.textSecondary}
                returnKeyType="done"
                maxLength={20}
                style={inputStyle}
              />
              {!!nameError && (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {nameError}
                </ThemedText>
              )}
              <ThemedText type="small" themeColor="textSecondary">
                只改称呼，不改能力：改名后它还是同一位助手，记得的事和正在留意的事都保留。
              </ThemedText>
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">怎么称呼你</ThemedText>
              <TextInput
                value={draft.ownerTitle ?? ''}
                onChangeText={(ownerTitle) => update({ ownerTitle })}
                accessibilityLabel="对主人的称呼"
                placeholder="如：妈妈、铲屎官（可留空）"
                placeholderTextColor={theme.textSecondary}
                returnKeyType="done"
                maxLength={20}
                style={inputStyle}
              />
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">语气详略</ThemedText>
              <View style={styles.chips}>
                {VERBOSITY_OPTIONS.map((option) => {
                  const selected = draft.verbosity === option.value;
                  return (
                    <Pressable
                      key={option.value}
                      accessibilityRole="button"
                      accessibilityState={{ selected }}
                      onPress={() => update({ verbosity: option.value })}
                      style={[
                        styles.chip,
                        {
                          borderColor: selected ? theme.tint : theme.backgroundSelected,
                          backgroundColor: selected ? theme.tintBackground : theme.background,
                        },
                      ]}
                    >
                      <ThemedText type="smallBold">{option.label}</ThemedText>
                    </Pressable>
                  );
                })}
              </View>
              <ThemedText type="small" themeColor="textSecondary">
                {VERBOSITY_OPTIONS.find((option) => option.value === draft.verbosity)?.hint}
              </ThemedText>
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">主动程度</ThemedText>
              <View style={styles.chips}>
                {PROACTIVITY_OPTIONS.map((option) => {
                  const selected = draft.proactivity === option.value;
                  return (
                    <Pressable
                      key={option.value}
                      accessibilityRole="button"
                      accessibilityState={{ selected }}
                      onPress={() => update({ proactivity: option.value })}
                      style={[
                        styles.chip,
                        {
                          borderColor: selected ? theme.tint : theme.backgroundSelected,
                          backgroundColor: selected ? theme.tintBackground : theme.background,
                        },
                      ]}
                    >
                      <ThemedText type="smallBold">{option.label}</ThemedText>
                    </Pressable>
                  );
                })}
              </View>
              <ThemedText type="small" themeColor="textSecondary">
                {PROACTIVITY_OPTIONS.find((option) => option.value === draft.proactivity)?.hint}
                。保存后即对周期回顾和设备提醒生效。
              </ThemedText>
            </View>
            {!!error && (
              <ThemedText type="small" style={{ color: theme.danger }}>
                {error}。修改已保留，可重试保存。
              </ThemedText>
            )}
            <Pressable
              onPress={() => void save()}
              disabled={saving || !loaded || !dirty || !!nameError}
              accessibilityRole="button"
              style={[
                styles.primary,
                {
                  backgroundColor: theme.tint,
                  opacity: saving || !loaded || !dirty || !!nameError ? 0.5 : 1,
                },
              ]}
            >
              {saving ? (
                <ActivityIndicator color={theme.backgroundElement} />
              ) : (
                <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                  保存设定
                </ThemedText>
              )}
            </Pressable>
          </>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
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
    padding: Spacing.three,
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
  card: { padding: Spacing.four, borderRadius: 24, gap: Spacing.three },
  input: { borderWidth: 1, borderRadius: 14, padding: Spacing.three, fontSize: 16, minHeight: 52 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  chip: { borderWidth: 1.5, borderRadius: 14, padding: 12, minHeight: 48 },
  primary: {
    minHeight: 52,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.three,
  },
});
