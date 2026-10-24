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
import { createPet, createPost, fetchPets } from '@/services/api';
import { bindPetgif, createPetgifPetSync, setPetAvatarImage } from '@/services/petgif';
import type { SocialPet } from '@/types/social';

type Mode = 'post' | 'pet';

const SPECIES = [
  { id: 'dog', label: '狗狗', emoji: '🐶' },
  { id: 'cat', label: '猫咪', emoji: '🐱' },
  { id: 'rabbit', label: '兔子', emoji: '🐰' },
] as const;

const PET_EMOJIS = ['🐾', '🐶', '🐱', '🐰', '🦮', '🐕‍🦺', '😺'];

/**
 * 创建页（底部弹层）：发帖 / 添加宠物。
 * 布局约定：sheet 撑满 → 内容统一 640 上限 + alignSelf 居中 + 水平边距 20，
 * 控件全部 width:'100%'，杜绝宽度收缩差异导致的贴边与横向拖动。
 */
export default function CreateScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [mode, setMode] = useState<Mode>('post');

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
            const selected = mode === item;
            return (
              <Pressable key={item} onPress={() => setMode(item)} style={styles.segmentItemWrap}>
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
      ) : mode === 'post' ? (
        <PostForm />
      ) : (
        <PetForm />
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

function PetForm() {
  const theme = useTheme();
  const router = useRouter();
  const [name, setName] = useState('');
  const [species, setSpecies] = useState<string>('dog');
  const [breed, setBreed] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [avatarEmoji, setAvatarEmoji] = useState('🐾');
  const [photo, setPhoto] = useState<{ uri: string; name: string; type: string } | null>(null);
  const [stage, setStage] = useState<'idle' | 'creating' | 'generating'>('idle');
  const [error, setError] = useState<string | null>(null);
  const sending = stage !== 'idle';

  const pickPhoto = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.9,
      allowsMultipleSelection: false,
    });
    const asset = result.assets?.[0];
    if (asset?.uri) {
      setPhoto({
        uri: asset.uri,
        name: asset.fileName ?? 'pet.jpg',
        type: asset.mimeType ?? 'image/jpeg',
      });
    }
  };

  const submit = async () => {
    if (!name.trim() || sending) return;
    setError(null);
    try {
      setStage('creating');
      const created = await createPet({
        name: name.trim(),
        species,
        breed: breed.trim(),
        avatarEmoji,
        deviceId: deviceId.trim(),
      });
      if (photo) {
        setStage('generating');
        const result = await createPetgifPetSync({ image: photo, name: name.trim() });
        await bindPetgif(created.pet.id, result.petgifId);
        if (result.characterCardUrl) {
          await setPetAvatarImage(created.pet.id, result.characterCardUrl);
        }
      }
      router.back();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '添加失败，请重试');
      setStage('idle');
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.form}>
      {/* AI 数字形象（可选）：上传照片，添加时同步生成角色卡 */}
      <Pressable
        onPress={pickPhoto}
        disabled={sending}
        style={[styles.imagePicker, { borderColor: theme.backgroundSelected }]}
      >
        {photo ? (
          <Image source={{ uri: photo.uri }} style={styles.imagePreview} contentFit="cover" />
        ) : (
          <View style={styles.imagePickerEmpty}>
            <SymbolView
              name={{ ios: 'camera.aperture', android: 'photo_camera', web: 'photo_camera' }}
              size={26}
              tintColor={theme.tint}
            />
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              上传照片 · 生成 AI 数字形象
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary" style={styles.photoHint}>
              可选，AI 将为它生成角色卡与 9 种动作
            </ThemedText>
          </View>
        )}
      </Pressable>

      <TextInput
        value={name}
        onChangeText={setName}
        placeholder="名字（必填）"
        placeholderTextColor={theme.textSecondary}
        maxLength={20}
        style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />

      <View style={styles.speciesRow}>
        {SPECIES.map((item) => {
          const selected = species === item.id;
          return (
            <Pressable
              key={item.id}
              onPress={() => setSpecies(item.id)}
              style={[styles.speciesChip, selected && { backgroundColor: theme.tint }]}
            >
              <ThemedText type="small" style={selected ? { color: '#ffffff' } : undefined}>
                {item.emoji} {item.label}
              </ThemedText>
            </Pressable>
          );
        })}
      </View>

      <TextInput
        value={breed}
        onChangeText={setBreed}
        placeholder="品种（可选）"
        placeholderTextColor={theme.textSecondary}
        maxLength={40}
        style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />
      <TextInput
        value={deviceId}
        onChangeText={setDeviceId}
        placeholder="项圈设备 ID（选填，机身标签可查，填写后自动绑定）"
        placeholderTextColor={theme.textSecondary}
        maxLength={40}
        autoCapitalize="none"
        style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />

      <ThemedText type="small" themeColor="textSecondary">
        头像 emoji
      </ThemedText>
      <View style={styles.emojiRow}>
        {PET_EMOJIS.map((emoji) => (
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
            <ThemedText style={{ fontSize: 22 }}>{emoji}</ThemedText>
          </Pressable>
        ))}
      </View>

      {error ? (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      ) : null}

      <Pressable
        onPress={submit}
        disabled={!name.trim() || sending}
        style={({ pressed }) => [
          styles.primaryButton,
          { backgroundColor: name.trim() ? theme.tint : theme.backgroundSelected },
          pressed && { opacity: 0.85 },
        ]}
      >
        {sending ? (
          <View style={styles.sendingRow}>
            <ActivityIndicator color="#ffffff" size="small" />
            <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
              {stage === 'generating' ? 'AI 正在生成数字形象…约 1 分钟' : '创建中…'}
            </ThemedText>
          </View>
        ) : (
          <ThemedText
            type="smallBold"
            style={{ color: name.trim() ? '#ffffff' : theme.textSecondary }}
          >
            {photo ? '添加并生成 AI 形象' : '添加宠物'}
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
  photoHint: {
    fontSize: 11,
    lineHeight: 15,
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

  field: {
    width: '100%',
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 12,
    fontSize: 15,
  },

  speciesRow: {
    flexDirection: 'row',
    gap: Spacing.two,
  },
  speciesChip: {
    flex: 1,
    alignItems: 'center',
    borderRadius: 14,
    paddingVertical: 10,
    backgroundColor: 'rgba(148,131,111,0.12)',
  },

  emojiRow: {
    width: '100%',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.two,
  },
  emojiCell: {
    width: 48,
    height: 48,
    borderRadius: 13,
    borderWidth: 1.5,
    borderColor: 'transparent',
    backgroundColor: 'rgba(148,131,111,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
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
