import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useRouter } from 'expo-router';
import { useState } from 'react';
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
import { normalizeForUpload } from '@/services/normalize-image';
import { publishStory, stageStory } from '@/services/social-publish';

type Picture = { uri: string; name: string; type: string };

export default function StoryCreateScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [picture, setPicture] = useState<Picture | null>(null);
  const [caption, setCaption] = useState('');
  const [audience, setAudience] = useState<'public' | 'followers'>('followers');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [mutationId, setMutationId] = useState('');

  const select = async (camera: boolean) => {
    try {
      if (camera) {
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) {
          setError('需要相机权限才能拍摄故事');
          return;
        }
      }
      const result = camera
        ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.85 })
        : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.85 });
      const asset = result.assets?.[0];
      if (asset?.uri) {
        setPicture(await normalizeForUpload(asset));
        setMutationId('');
        setError('');
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '选择图片失败');
    }
  };

  const publish = () => {
    if (!picture || sending) {
      setError('请先拍摄或选择一张照片');
      return;
    }
    if (!user) {
      setError('登录后才能发布故事');
      return;
    }
    setSending(true);
    setError('');
    const id = mutationId || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setMutationId(id);
    // 乐观发布：故事先落本地占位（故事页立刻能看到），上传在后台跑
    const entry = stageStory({ image: picture, caption: caption.trim(), audience }, id, {
      id: user.id,
      displayName: user.displayName,
      avatarEmoji: user.avatarEmoji,
    });
    router.back();
    publishStory(entry);
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()}>
            <ThemedText style={{ color: theme.tintForeground }}>取消</ThemedText>
          </Pressable>
          <ThemedText type="subtitle">发布故事</ThemedText>
          <View style={styles.spacer} />
        </View>
        <ThemedText type="small" themeColor="textSecondary">
          故事发布后展示 24 小时
        </ThemedText>
        {picture ? (
          <Image source={{ uri: picture.uri }} style={styles.preview} contentFit="cover" />
        ) : (
          <View
            style={[
              styles.preview,
              styles.placeholder,
              { backgroundColor: theme.backgroundSelected },
            ]}
          >
            <ThemedText type="small" themeColor="textSecondary">
              选择故事照片
            </ThemedText>
          </View>
        )}
        <View style={styles.row}>
          <Pressable
            onPress={() => void select(false)}
            style={[styles.button, { backgroundColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold">相册</ThemedText>
          </Pressable>
          <Pressable
            onPress={() => void select(true)}
            style={[styles.button, { backgroundColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold">拍照</ThemedText>
          </Pressable>
        </View>
        <TextInput
          value={caption}
          onChangeText={setCaption}
          multiline
          maxLength={300}
          placeholder="配一句话…"
          placeholderTextColor={theme.textSecondary}
          style={[styles.caption, { color: theme.text, borderColor: theme.backgroundSelected }]}
        />
        <View style={styles.row}>
          {(['followers', 'public'] as const).map((item) => (
            <Pressable
              key={item}
              onPress={() => setAudience(item)}
              style={[
                styles.button,
                { backgroundColor: audience === item ? theme.tint : theme.backgroundSelected },
              ]}
            >
              <ThemedText
                type="smallBold"
                style={{ color: audience === item ? theme.onTint : theme.text }}
              >
                {item === 'followers' ? '仅粉丝可见' : '公开可见'}
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
          onPress={publish}
          disabled={sending}
          style={[styles.publish, { backgroundColor: theme.tint }]}
        >
          {sending ? (
            <ActivityIndicator color={theme.onTint} />
          ) : (
            <ThemedText type="smallBold" style={{ color: theme.onTint }}>
              发布故事
            </ThemedText>
          )}
        </Pressable>
      </ScrollView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    padding: Spacing.four,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
    gap: Spacing.three,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  spacer: { width: 32 },
  preview: { width: '100%', aspectRatio: 3 / 4, borderRadius: 16 },
  placeholder: { alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', gap: Spacing.two },
  button: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, borderRadius: 14 },
  caption: { borderWidth: 1, borderRadius: 14, minHeight: 70, padding: Spacing.three },
  publish: { alignItems: 'center', padding: Spacing.three, borderRadius: 14 },
});
