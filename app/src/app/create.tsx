import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
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
import { createPost, fetchPets } from '@/services/api';
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
                  if (item === 'pet') router.replace('/setup');
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
                    {item === 'post' ? '新帖子' : '添加宠物'}
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
        <PostForm />
      )}
    </KeyboardAvoidingView>
  );
}

function PostForm() {
  const theme = useTheme();
  const router = useRouter();
  const [image, setImage] = useState<{ uri: string; name: string; type: string } | null>(null);
  const [caption, setCaption] = useState('');
  const [location, setLocation] = useState('');
  const [pets, setPets] = useState<SocialPet[]>([]);
  const [petId, setPetId] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pickImage = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.8,
      allowsMultipleSelection: false,
    });
    const asset = result.assets?.[0];
    if (asset?.uri) {
      setImage({
        uri: asset.uri,
        name: asset.fileName ?? 'photo.jpg',
        type: asset.mimeType ?? 'image/jpeg',
      });
      if (pets.length === 0) {
        fetchPets()
          .then((data) => {
            setPets(data.pets);
            setPetId(data.pets[0]?.id ?? null);
          })
          .catch(() => {
            // 无宠物也能发帖
          });
      }
    }
  };

  const submit = async () => {
    if (!image || sending) return;
    setSending(true);
    setError(null);
    try {
      await createPost({
        image,
        caption: caption.trim(),
        location: location.trim(),
        petId: petId ?? undefined,
      });
      router.back();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '发布失败，请重试');
      setSending(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.form}>
      <Pressable
        onPress={pickImage}
        style={[styles.imagePicker, { borderColor: theme.backgroundSelected }]}
      >
        {image ? (
          <Image source={{ uri: image.uri }} style={styles.imagePreview} contentFit="cover" />
        ) : (
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
              选择一张照片
            </ThemedText>
          </View>
        )}
      </Pressable>

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
                onPress={() => setPetId(pet.id === petId ? null : pet.id)}
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
        onChangeText={setCaption}
        placeholder="记录和它的这一刻…"
        placeholderTextColor={theme.textSecondary}
        multiline
        maxLength={500}
        style={[styles.captionInput, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />
      <ThemedText type="small" themeColor="textSecondary" style={styles.counter}>
        {caption.length}/500
      </ThemedText>

      <View style={[styles.locationRow, { borderColor: theme.backgroundSelected }]}>
        <SymbolView
          name={{ ios: 'mappin.and.ellipse', android: 'place', web: 'place' }}
          size={16}
          tintColor={theme.tint}
        />
        <TextInput
          value={location}
          onChangeText={setLocation}
          placeholder="添加位置（可选）"
          placeholderTextColor={theme.textSecondary}
          maxLength={80}
          style={styles.locationInput}
        />
      </View>

      {error ? (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      ) : null}

      <Pressable
        onPress={submit}
        disabled={!image || sending}
        style={({ pressed }) => [
          styles.primaryButton,
          { backgroundColor: image ? theme.tint : theme.backgroundSelected },
          pressed && { opacity: 0.85 },
        ]}
      >
        {sending ? (
          <ActivityIndicator color="#ffffff" />
        ) : (
          <ThemedText type="smallBold" style={{ color: image ? '#ffffff' : theme.textSecondary }}>
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
    width: '100%',
    height: '100%',
  },

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
  counter: {
    alignSelf: 'flex-end',
    marginTop: -Spacing.two,
    fontSize: 11,
  },
  locationRow: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
  },
  locationInput: {
    flex: 1,
    paddingVertical: 12,
    fontSize: 15,
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
