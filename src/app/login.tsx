import { AppIcon } from '@/components/ui/icon';
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
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';

const USER_EMOJIS = ['🧑‍🌾', '🧑‍🎓', '👩‍💻', '🧑‍🍳', '🧑‍🚀', '🦊', '🐨', '🐼'];

/** 登录 / 注册弹层。 */
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
      setError(exc instanceof Error ? exc.message : '登录失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = { color: theme.text, borderColor: theme.backgroundSelected };

  return (
    <KeyboardAvoidingView
      style={[
        styles.sheet,
        { backgroundColor: theme.background, paddingTop: insets.top + Spacing.three },
      ]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityLabel="关闭">
          <AppIcon name="close-circle" size={26} color={theme.textSecondary} />
        </Pressable>
        <ThemedText type="subtitle" style={styles.title}>
          {mode === 'login' ? '欢迎回来' : '加入 PetSense'}
        </ThemedText>
        <View style={{ width: 26 }} />
      </View>

      <ScrollView
        contentContainerStyle={[styles.form, { paddingBottom: insets.bottom + Spacing.five }]}
      >
        <View style={[styles.segment, { backgroundColor: theme.backgroundSelected }]}>
          {(['login', 'register'] as const).map((item) => (
            <Pressable
              key={item}
              onPress={() => {
                setMode(item);
                setError(null);
              }}
            >
              <View
                style={[
                  styles.segmentItem,
                  mode === item && { backgroundColor: theme.background, elevation: 2 },
                ]}
              >
                <ThemedText
                  type="smallBold"
                  style={mode === item ? { color: theme.tint } : undefined}
                >
                  {item === 'login' ? '登录' : '注册'}
                </ThemedText>
              </View>
            </Pressable>
          ))}
        </View>

        <TextInput
          value={username}
          onChangeText={setUsername}
          placeholder="账号（2–20 位中文 / 字母 / 数字 / 下划线）"
          placeholderTextColor={theme.textSecondary}
          autoCapitalize="none"
          style={[styles.field, inputStyle]}
        />
        {mode === 'register' && (
          <>
            <TextInput
              value={displayName}
              onChangeText={setDisplayName}
              placeholder="昵称（默认同账号）"
              placeholderTextColor={theme.textSecondary}
              maxLength={20}
              style={[styles.field, inputStyle]}
            />
            <View style={styles.emojiRow}>
              {USER_EMOJIS.map((emoji) => (
                <Pressable
                  key={emoji}
                  onPress={() => setAvatarEmoji(emoji)}
                  style={[styles.emojiCell, avatarEmoji === emoji && { borderColor: theme.tint }]}
                >
                  <ThemedText style={{ fontSize: 22 }}>{emoji}</ThemedText>
                </Pressable>
              ))}
            </View>
          </>
        )}
        <TextInput
          value={password}
          onChangeText={setPassword}
          placeholder="密码（6–72 位）"
          placeholderTextColor={theme.textSecondary}
          secureTextEntry
          style={[styles.field, inputStyle]}
        />

        {error && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        )}

        <Pressable
          onPress={submit}
          disabled={busy || !username.trim() || password.length < 6}
          style={({ pressed }) => [
            styles.submit,
            {
              backgroundColor:
                username.trim() && password.length >= 6 ? theme.tint : theme.backgroundSelected,
            },
            pressed && { opacity: 0.85 },
          ]}
        >
          {busy ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <ThemedText
              type="smallBold"
              style={{
                color: username.trim() && password.length >= 6 ? '#ffffff' : theme.textSecondary,
              }}
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
  sheet: { flex: 1, alignItems: 'center' },
  header: {
    width: '100%',
    maxWidth: MaxContentWidth,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.three,
  },
  title: { fontSize: 22 },
  form: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    gap: Spacing.three,
  },
  segment: { flexDirection: 'row', borderRadius: 14, padding: 3, alignSelf: 'center' },
  segmentItem: { borderRadius: 11, paddingHorizontal: Spacing.four, paddingVertical: 6 },
  field: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
    fontSize: 14,
  },
  emojiRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  emojiCell: {
    width: 48,
    height: 48,
    borderRadius: 13,
    borderWidth: 1.5,
    borderColor: 'transparent',
    backgroundColor: 'rgba(148, 131, 111, 0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  submit: { borderRadius: 16, alignItems: 'center', paddingVertical: 12, marginTop: Spacing.one },
});
