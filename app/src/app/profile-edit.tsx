import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchUserProfile, updatePublicProfile } from '@/services/api';

export default function ProfileEditScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, refreshUser } = useAuth();
  const [displayName, setDisplayName] = useState('');
  const [bio, setBio] = useState('');
  const [avatarEmoji, setAvatarEmoji] = useState('🙂');
  const [version, setVersion] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    if (!user) return;
    void fetchUserProfile(user.id)
      .then(({ profile }) => {
        if (!active) return;
        setDisplayName(profile.displayName);
        setBio(profile.bio);
        setAvatarEmoji(profile.avatarEmoji);
        setVersion(profile.version ?? null);
        setError('');
      })
      .catch((exc) => {
        if (active) setError(exc instanceof Error ? exc.message : '资料加载失败');
      });
    return () => {
      active = false;
    };
  }, [user]);

  const save = async () => {
    if (version == null || saving) return;
    setSaving(true);
    setError('');
    try {
      await updatePublicProfile({
        displayName: displayName.trim(),
        bio: bio.trim(),
        avatarEmoji: avatarEmoji.trim(),
        version,
      });
      await refreshUser();
      router.back();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()} accessibilityLabel="取消编辑">
            <SymbolView
              name={{ ios: 'xmark', android: 'close', web: 'close' }}
              size={24}
              tintColor={theme.tint}
            />
          </Pressable>
          <ThemedText type="subtitle">编辑公开资料</ThemedText>
          <View style={styles.spacer} />
        </View>
        {version == null && !error && <ActivityIndicator color={theme.tint} />}
        <ThemedText type="smallBold">展示名</ThemedText>
        <TextInput
          value={displayName}
          onChangeText={setDisplayName}
          maxLength={40}
          style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
        />
        <ThemedText type="smallBold">头像表情</ThemedText>
        <TextInput
          value={avatarEmoji}
          onChangeText={setAvatarEmoji}
          maxLength={16}
          style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
        />
        <ThemedText type="smallBold">简介</ThemedText>
        <TextInput
          value={bio}
          onChangeText={setBio}
          maxLength={300}
          multiline
          style={[
            styles.input,
            styles.bio,
            { color: theme.text, borderColor: theme.backgroundSelected },
          ]}
        />
        {!!error && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        )}
        <Pressable
          onPress={() => void save()}
          disabled={version == null || saving}
          style={[styles.save, { backgroundColor: theme.tint }]}
        >
          <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
            {saving ? '保存中…' : '保存资料'}
          </ThemedText>
        </Pressable>
      </ScrollView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    padding: Spacing.four,
    gap: Spacing.three,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  spacer: { width: 24 },
  input: { borderWidth: 1, borderRadius: 14, padding: Spacing.three },
  bio: { minHeight: 100, textAlignVertical: 'top' },
  save: { alignItems: 'center', padding: Spacing.three, borderRadius: 14 },
});
