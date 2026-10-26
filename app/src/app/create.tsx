import { Image } from 'expo-image';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Directory, File, Paths } from 'expo-file-system';
import * as ImagePicker from 'expo-image-picker';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
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
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import { createPost, fetchPets } from '@/services/api';
import { normalizeForUpload } from '@/services/normalize-image';
import type { SocialPet } from '@/types/social';

/**
 * 创建页（底部弹层）：发帖；宠物建档入口转到独立设置流程。
 * 布局约定：sheet 撑满 → 内容统一 640 上限 + alignSelf 居中 + 水平边距 20，
 * 控件全部 width:'100%'，杜绝宽度收缩差异导致的贴边与横向拖动。
 */
export default function CreateScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();

  return (
    <KeyboardAvoidingView
      style={[styles.sheet, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      {/* 头部：关闭 + 分段控件 */}
      <View style={[styles.header, { paddingTop: insets.top + Spacing.two }]}>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityLabel="关闭">
          <SymbolView
            name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
            size={26}
            tintColor={theme.textSecondary}
          />
        </Pressable>
        <View style={[styles.segment, { backgroundColor: theme.backgroundSelected }]}>
          {(['post', 'pet'] as const).map((item) => {
            const selected = item === 'post';
            return (
              <Pressable
                key={item}
                onPress={() => {
                  if (item === 'pet') router.replace('/pet');
                }}
                style={styles.segmentItemWrap}
              >
                <View
                  style={[
                    styles.segmentItem,
                    selected && { backgroundColor: theme.background, boxShadow: Shadows.chip },
                  ]}
                >
                  <ThemedText type="smallBold" style={selected ? { color: theme.tint } : undefined}>
                    {item === 'post' ? '新帖子' : '宠物档案'}
                  </ThemedText>
                </View>
              </Pressable>
            );
          })}
        </View>
        <View style={{ width: 26 }} />
      </View>

      {!user ? (
        <ScrollView contentContainerStyle={styles.form}>
          <View style={styles.loginGuide}>
            <ThemedText style={{ fontSize: 40 }}>🔐</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              登录后可以发帖和添加宠物
            </ThemedText>
            <Pressable
              onPress={() => router.push('/login')}
              style={[styles.primaryButton, { backgroundColor: theme.tint }]}
            >
              <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                去登录
              </ThemedText>
            </Pressable>
          </View>
        </ScrollView>
      ) : (
        <PostForm key={user.id} />
      )}
    </KeyboardAvoidingView>
  );
}

function PostForm() {
  const theme = useTheme();
  const router = useRouter();
  const [images, setImages] = useState<{ uri: string; name: string; type: string }[]>([]);
  const [caption, setCaption] = useState('');
  const [location, setLocation] = useState('');
  const [audience, setAudience] = useState<'public' | 'followers'>('public');
  const [draftReady, setDraftReady] = useState(false);
  const [mutationId, setMutationId] = useState('');
  const [pets, setPets] = useState<SocialPet[]>([]);
  const [petId, setPetId] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { user } = useAuth();
  const draftKey = `petsense.community.draft.${user?.id ?? 'guest'}`;
  const draftDirectory =
    Platform.OS === 'web'
      ? null
      : new Directory(Paths.document, 'community-drafts', String(user?.id ?? 'guest'));

  useEffect(() => {
    let active = true;
    void AsyncStorage.getItem(draftKey)
      .then((value) => {
        if (!active || !value) return;
        const saved = JSON.parse(value) as {
          caption?: string;
          location?: string;
          petId?: number | null;
          audience?: 'public' | 'followers';
          mutationId?: string;
          images?: { uri: string; name: string; type: string }[];
        };
        setCaption(saved.caption ?? '');
        setLocation(saved.location ?? '');
        setPetId(saved.petId ?? null);
        setAudience(saved.audience ?? 'public');
        if (saved.mutationId) setMutationId(saved.mutationId);
        if (Platform.OS !== 'web' && saved.images?.length) {
          const available = saved.images.filter((item) => new File(item.uri).exists);
          setImages(available);
          if (available.length !== saved.images.length)
            setError('部分草稿照片已丢失，请重新选择。');
        }
      })
      .catch(() => setError('草稿读取失败，请稍后重试。'))
      .finally(() => {
        if (active) setDraftReady(true);
      });
    return () => {
      active = false;
    };
  }, [draftKey]);

  useEffect(() => {
    if (!draftReady) return;
    void AsyncStorage.setItem(
      draftKey,
      JSON.stringify({
        caption,
        location,
        petId,
        audience,
        mutationId,
        images: Platform.OS === 'web' ? [] : images,
      }),
    ).catch(() => setError('草稿保存失败，请检查设备存储空间。'));
  }, [draftKey, draftReady, caption, location, petId, audience, mutationId, images]);
  const sequence = useRef(0);
  const loadPets = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) return;
    try {
      const data = await fetchPets();
      if (current !== sequence.current) return;
      setPets(data.pets);
      setPetId((selected) =>
        data.pets.some((pet) => pet.id === selected) ? selected : (data.pets[0]?.id ?? null),
      );
    } catch {
      /* 下次进入页面或资料更新后重试，不阻断发帖。 */
    }
  }, [user]);
  usePetChanges(loadPets);
  useFocusEffect(
    useCallback(() => {
      void loadPets();
      return () => {
        sequence.current++;
      };
    }, [loadPets]),
  );

  const pickImages = async (camera: boolean) => {
    try {
      if (camera) {
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) {
          setError('需要相机权限才能拍照');
          return;
        }
      }
      const result = camera
        ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.8 })
        : await ImagePicker.launchImageLibraryAsync({
            mediaTypes: ['images'],
            quality: 0.8,
            allowsMultipleSelection: true,
            selectionLimit: 10 - images.length,
          });
      const picked = (result.assets ?? []).filter((asset) => !!asset.uri);
      if (picked.length) {
        const selected = await Promise.all(picked.map(normalizeForUpload));
        let durable = selected;
        if (Platform.OS !== 'web') {
          if (!draftDirectory) throw new Error('草稿目录不可用');
          draftDirectory.create({ idempotent: true, intermediates: true });
          durable = [];
          for (const image of selected) {
            const destination = new File(
              draftDirectory,
              `${Date.now()}-${Math.random().toString(36).slice(2)}.jpg`,
            );
            await new File(image.uri).copy(destination);
            durable.push({ ...image, uri: destination.uri });
          }
        }
        setImages((current) => [...current, ...durable].slice(0, 10));
        setMutationId('');
        setError(null);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '选择照片失败');
    }
  };

  const submit = async () => {
    if (sending) return;
    if (!images.length && !caption.trim()) {
      setError('请先写点内容或选择照片');
      return;
    }
    setSending(true);
    setError(null);
    const id = mutationId || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setMutationId(id);
    try {
      await createPost({
        images,
        caption: caption.trim(),
        location: location.trim(),
        petId: petId ?? undefined,
        audience,
        mutationId: id,
      });
      await AsyncStorage.removeItem(draftKey).catch(() => {});
      try {
        if (draftDirectory?.exists) draftDirectory.delete();
      } catch {
        // 发布已确认成功，草稿文件清理失败不改变发布结果。
      }
      router.back();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '发布失败，请重试');
    } finally {
      setSending(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.form}>
      <View style={[styles.imagePicker, { borderColor: theme.backgroundSelected }]}>
        <View style={styles.imagePickerEmpty}>
          <SymbolView
            name={{
              ios: 'photo.on.rectangle.angled',
              android: 'photo_library',
              web: 'photo_library',
            }}
            size={30}
            tintColor={theme.textSecondary}
          />
          <ThemedText type="small" themeColor="textSecondary">
            {images.length ? `已选择 ${images.length}/10 张照片` : '可选择照片，也可只发文字'}
          </ThemedText>
        </View>
      </View>
      <View style={styles.mediaButtons}>
        <Pressable
          onPress={() => void pickImages(false)}
          style={[styles.mediaButton, { backgroundColor: theme.backgroundSelected }]}
        >
          <ThemedText type="smallBold">从相册选择</ThemedText>
        </Pressable>
        <Pressable
          onPress={() => void pickImages(true)}
          style={[styles.mediaButton, { backgroundColor: theme.backgroundSelected }]}
        >
          <ThemedText type="smallBold">拍照</ThemedText>
        </Pressable>
      </View>
      {!!images.length && (
        <ScrollView horizontal contentContainerStyle={styles.mediaRow}>
          {images.map((image, index) => (
            <View key={`${image.uri}-${index}`}>
              <Image source={{ uri: image.uri }} style={styles.imagePreview} contentFit="cover" />
              <Pressable
                onPress={() => {
                  setImages((current) => current.filter((_, i) => i !== index));
                  setMutationId('');
                  if (Platform.OS !== 'web' && new File(image.uri).exists)
                    new File(image.uri).delete();
                }}
              >
                <ThemedText type="small" style={{ color: theme.danger }}>
                  移除
                </ThemedText>
              </Pressable>
            </View>
          ))}
        </ScrollView>
      )}

      {pets.length > 0 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.petRow}
        >
          {pets.map((pet) => {
            const selected = pet.id === petId;
            return (
              <Pressable
                key={pet.id}
                onPress={() => {
                  setPetId(pet.id === petId ? null : pet.id);
                  setMutationId('');
                }}
                style={[styles.petChip, selected && { backgroundColor: theme.tint }]}
              >
                <ThemedText type="small" style={selected ? { color: '#ffffff' } : undefined}>
                  {pet.avatar_emoji} {pet.name}
                </ThemedText>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      <TextInput
        value={caption}
        onChangeText={(value) => {
          setCaption(value);
          setMutationId('');
        }}
        placeholder="记录和它的这一刻…"
        placeholderTextColor={theme.textSecondary}
        multiline
        maxLength={500}
        style={[styles.captionInput, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />
      <ThemedText type="small" themeColor="textSecondary" style={styles.counter}>
        {caption.length}/500
      </ThemedText>

      <TextInput
        value={location}
        onChangeText={(value) => {
          setLocation(value);
          setMutationId('');
        }}
        placeholder="地点名称（选填，不会使用你的实时位置）"
        placeholderTextColor={theme.textSecondary}
        maxLength={80}
        style={[styles.locationInput, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />

      <View style={styles.mediaButtons}>
        {(['public', 'followers'] as const).map((item) => (
          <Pressable
            key={item}
            onPress={() => {
              setAudience(item);
              setMutationId('');
            }}
            style={[
              styles.mediaButton,
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
      <ThemedText type="small" themeColor="textSecondary">
        {Platform.OS === 'web'
          ? '草稿会保存文字和可见范围；网页刷新后照片需重新选择。'
          : '草稿会保存文字、照片和可见范围，可稍后继续编辑。'}
      </ThemedText>

      {error ? (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      ) : null}

      <Pressable
        onPress={submit}
        disabled={sending}
        style={({ pressed }) => [
          styles.primaryButton,
          { backgroundColor: theme.tint },
          pressed && { opacity: 0.85 },
        ]}
      >
        {sending ? (
          <ActivityIndicator color="#ffffff" />
        ) : (
          <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
            分享
          </ThemedText>
        )}
      </Pressable>
    </ScrollView>
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
  segment: {
    flexDirection: 'row',
    borderRadius: 14,
    padding: 3,
  },
  segmentItemWrap: {
    minWidth: 88,
  },
  segmentItem: {
    alignItems: 'center',
    borderRadius: 11,
    paddingVertical: 7,
    paddingHorizontal: Spacing.three,
  },

  // 表单：统一定宽（100%）+ 内容居中限宽 + 水平边距，杜绝宽度收缩差异
  form: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.three,
    paddingBottom: Spacing.six,
    gap: Spacing.three,
  },

  loginGuide: {
    alignItems: 'center',
    gap: Spacing.two,
    paddingVertical: Spacing.six,
  },

  imagePicker: {
    width: '100%',
    height: 170,
    borderRadius: 18,
    borderWidth: 1.5,
    borderStyle: 'dashed',
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  imagePickerEmpty: {
    alignItems: 'center',
    gap: Spacing.two,
  },
  imagePreview: {
    width: 96,
    height: 96,
    borderRadius: 12,
  },
  mediaButtons: { flexDirection: 'row', gap: Spacing.two },
  mediaButton: { borderRadius: 14, padding: Spacing.two },
  mediaRow: { gap: Spacing.two },

  petRow: {
    gap: Spacing.two,
  },
  petChip: {
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 7,
    backgroundColor: 'rgba(148,131,111,0.12)',
  },

  captionInput: {
    width: '100%',
    minHeight: 100,
    borderWidth: 1,
    borderRadius: 14,
    padding: Spacing.three,
    textAlignVertical: 'top',
    fontSize: 15,
  },
  locationInput: {
    width: '100%',
    borderWidth: 1,
    borderRadius: 14,
    padding: Spacing.three,
    fontSize: 15,
  },
  counter: {
    alignSelf: 'flex-end',
    marginTop: -Spacing.two,
    fontSize: 11,
  },

  primaryButton: {
    width: '100%',
    borderRadius: 16,
    alignItems: 'center',
    paddingVertical: 14,
    marginTop: Spacing.one,
  },
  sendingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
});
