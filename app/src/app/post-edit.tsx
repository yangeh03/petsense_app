import { SymbolView } from 'expo-symbols';
import { useLocalSearchParams, useRouter } from 'expo-router';
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
import { editPost, fetchPost } from '@/services/api';
import type { Post } from '@/types/social';

export default function PostEditScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const viewerId = user?.id ?? 0;
  const params = useLocalSearchParams<{ id: string }>();
  const id = Number(params.id);
  const [loadedPost, setOriginal] = useState<Post | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const original = loadedFor === viewerId ? loadedPost : null;
  const [caption, setCaption] = useState('');
  const [location, setLocation] = useState('');
  const [audience, setAudience] = useState<'public' | 'followers'>('public');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    if (!Number.isSafeInteger(id) || id <= 0) return;
    void fetchPost(id)
      .then((data) => {
        if (!active) return;
        if (!data.post.isMine) {
          setOriginal(null);
          setLoadedFor(viewerId);
          setError('只能编辑自己的帖子');
          return;
        }
        setOriginal(data.post);
        setLoadedFor(viewerId);
        setCaption(data.post.caption);
        setLocation(data.post.location_text);
        setAudience(data.post.audience ?? 'public');
      })
      .catch((exc) => {
        if (active) {
          setLoadedFor(viewerId);
          setOriginal(null);
          setError(exc instanceof Error ? exc.message : '帖子加载失败');
        }
      });
    return () => {
      active = false;
    };
  }, [id, viewerId]);

  const save = async () => {
    if (!original?.version || saving) return;
    setSaving(true);
    setError('');
    try {
      await editPost(id, {
        caption: caption.trim(),
        location: location.trim(),
        audience,
        version: original.version,
      });
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
          <ThemedText type="subtitle">编辑帖子</ThemedText>
          <View style={styles.spacer} />
        </View>
        {!original && !error ? <ActivityIndicator color={theme.tint} /> : null}
        {original && (
          <>
            <TextInput
              value={caption}
              onChangeText={setCaption}
              multiline
              maxLength={500}
              placeholder="帖子文案"
              placeholderTextColor={theme.textSecondary}
              style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            />
            <ThemedText type="small" themeColor="textSecondary">
              {caption.length}/500
            </ThemedText>
            <TextInput
              value={location}
              onChangeText={setLocation}
              maxLength={80}
              placeholder="地点名称（选填）"
              placeholderTextColor={theme.textSecondary}
              style={[
                styles.locationInput,
                { color: theme.text, borderColor: theme.backgroundSelected },
              ]}
            />
            <View style={styles.row}>
              {(['public', 'followers'] as const).map((item) => (
                <Pressable
                  key={item}
                  onPress={() => setAudience(item)}
                  style={[
                    styles.option,
                    { backgroundColor: audience === item ? theme.tint : theme.backgroundSelected },
                  ]}
                >
                  <ThemedText
                    type="smallBold"
                    style={{ color: audience === item ? '#ffffff' : theme.text }}
                  >
                    {item === 'public' ? '公开' : '仅粉丝可见'}
                  </ThemedText>
                </Pressable>
              ))}
            </View>
            {!!error && (
              <ThemedText type="small" style={{ color: theme.danger }}>
                {error}
              </ThemedText>
            )}
            <Pressable
              onPress={() => void save()}
              disabled={!original || saving}
              style={[styles.save, { backgroundColor: theme.tint }]}
            >
              <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                {saving ? '保存中…' : '保存'}
              </ThemedText>
            </Pressable>
          </>
        )}
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
  input: {
    borderWidth: 1,
    borderRadius: 14,
    minHeight: 160,
    padding: Spacing.three,
    textAlignVertical: 'top',
  },
  locationInput: { borderWidth: 1, borderRadius: 14, padding: Spacing.three },
  row: { flexDirection: 'row', gap: Spacing.two },
  option: { padding: Spacing.two, borderRadius: 12 },
  save: { alignItems: 'center', padding: Spacing.three, borderRadius: 14 },
});
