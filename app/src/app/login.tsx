import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useState } from 'react';
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
import { Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';

const USER_EMOJIS = ['🧑‍🌾', '🧑‍🎓', '👩‍💻', '🧑‍🍳', '🧑‍🚀', '🦊', '🐨', '🐼'];

/**
 * 登录 / 注册弹层。
 * 布局与创建页同构：sheet 撑满，表单 width:100% + 限宽居中 + 水平边距，
 * 控件显式定宽——避免宽度收缩导致的贴边与横向拖动。
 */
export default function LoginScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [avatarEmoji, setAvatarEmoji] = useState(USER_EMOJIS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === 'login') {
        await login(username.trim(), password);
      } else {
        await register({
          username: username.trim(),
          displayName: displayName.trim() || username.trim(),
          password,
          avatarEmoji,
        });
      }
      router.back();
    } catch (exc) {
      // 网络层失败（请求未到达服务器）与业务错误（账号/密码）分开提示
      const message = exc instanceof Error ? exc.message : '';
      if (/network|fetch|timeout|abort/i.test(message) || !(exc instanceof Error)) {
        setError('网络连接失败，请检查网络后重试');
      } else {
        setError(message || '登录失败，请重试');
      }
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = username.trim().length > 0 && password.length >= 6;

  return (
    <KeyboardAvoidingView
      style={[styles.sheet, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      {/* 头部：关闭 + 标题 */}
      <View style={[styles.header, { paddingTop: insets.top + Spacing.two }]}>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityLabel="关闭">
          <SymbolView
            name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
            size={26}
            tintColor={theme.textSecondary}
          />
        </Pressable>
        <ThemedText type="subtitle" style={styles.title}>
          {mode === 'login' ? '欢迎回来' : '加入 PetSense'}
        </ThemedText>
        <View style={{ width: 26 }} />
      </View>

      <ScrollView
        contentContainerStyle={[styles.form, { paddingBottom: insets.bottom + Spacing.six }]}
        keyboardShouldPersistTaps="handled"
      >
        {/* 分段控件 */}
        <View style={[styles.segment, { backgroundColor: theme.backgroundSelected }]}>
          {(['login', 'register'] as const).map((item) => {
            const selected = mode === item;
            return (
              <Pressable
                key={item}
                style={styles.segmentItemWrap}
                onPress={() => {
                  setMode(item);
                  setError(null);
                }}
              >
                <View
                  style={[
                    styles.segmentItem,
                    selected && { backgroundColor: theme.background, boxShadow: Shadows.chip },
                  ]}
                >
                  <ThemedText type="smallBold" style={selected ? { color: theme.tint } : undefined}>
                    {item === 'login' ? '登录' : '注册'}
                  </ThemedText>
                </View>
              </Pressable>
            );
          })}
        </View>

        <TextInput
          value={username}
          onChangeText={setUsername}
          placeholder="账号（2–20 位中文 / 字母 / 数字 / 下划线）"
          placeholderTextColor={theme.textSecondary}
          autoCapitalize="none"
          style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
        />
        {mode === 'register' && (
          <>
            <TextInput
              value={displayName}
              onChangeText={setDisplayName}
              placeholder="昵称（默认同账号）"
              placeholderTextColor={theme.textSecondary}
              maxLength={20}
              style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
            />
            <View style={styles.emojiRow}>
              <ThemedText type="small" themeColor="textSecondary">
                头像
              </ThemedText>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.emojiList}
              >
                {USER_EMOJIS.map((emoji) => (
                  <Pressable
                    key={emoji}
                    onPress={() => setAvatarEmoji(emoji)}
                    style={[
                      styles.emojiCell,
                      avatarEmoji === emoji && {
                        borderColor: theme.tint,
                        backgroundColor: theme.tintBackground,
                      },
                    ]}
                  >
                    <ThemedText style={{ fontSize: 20 }}>{emoji}</ThemedText>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          </>
        )}
        <TextInput
          value={password}
          onChangeText={setPassword}
          placeholder="密码（6–72 位）"
          placeholderTextColor={theme.textSecondary}
          secureTextEntry
          style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
        />

        {error ? (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        ) : null}

        <Pressable
          onPress={submit}
          disabled={busy || !canSubmit}
          style={({ pressed }) => [
            styles.submit,
            {
              backgroundColor: canSubmit ? theme.tint : theme.backgroundSelected,
            },
            pressed && { opacity: 0.85 },
          ]}
        >
          {busy ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <ThemedText
              type="smallBold"
              style={{ color: canSubmit ? '#ffffff' : theme.textSecondary }}
            >
              {mode === 'login' ? '登录' : '注册并登录'}
            </ThemedText>
          )}
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  sheet: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.three,
    paddingBottom: Spacing.two,
  },
  title: { fontSize: 20 },

  form: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.three,
    gap: Spacing.three,
  },
  segment: {
    flexDirection: 'row',
    borderRadius: 14,
    padding: 3,
    alignSelf: 'center',
  },
  segmentItemWrap: {
    minWidth: 96,
  },
  segmentItem: {
    alignItems: 'center',
    borderRadius: 11,
    paddingVertical: 7,
    paddingHorizontal: Spacing.three,
  },
  field: {
    width: '100%',
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 12,
    fontSize: 15,
  },
  emojiRow: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  emojiList: {
    gap: Spacing.two,
  },
  emojiCell: {
    width: 42,
    height: 42,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: 'transparent',
    backgroundColor: 'rgba(148,131,111,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  submit: {
    width: '100%',
    borderRadius: 16,
    alignItems: 'center',
    paddingVertical: 14,
    marginTop: Spacing.one,
  },
});
