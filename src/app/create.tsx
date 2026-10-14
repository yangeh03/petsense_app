import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
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
import { createPet, createPost, fetchPets } from '@/services/api';
import type { SocialPet } from '@/types/social';

type Mode = 'post' | 'pet';

const SPECIES = [
  { id: 'dog', label: '狗狗', emoji: '🐶' },
  { id: 'cat', label: '猫咪', emoji: '🐱' },
  { id: 'rabbit', label: '兔子', emoji: '🐰' },
] as const;

const PET_EMOJIS = ['🐾', '🐶', '🐱', '🐰', '🦮', '🐕‍🦺', '😺'];

/** 创建页（底部弹层）：发帖 / 添加宠物。 */
export default function CreateScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [mode, setMode] = useState<Mode>('post');

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
        <View style={[styles.segment, { backgroundColor: theme.backgroundSelected }]}>
          {(['post', 'pet'] as const).map((item) => (
            <Pressable key={item} onPress={() => setMode(item)}>
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
                  {item === 'post' ? '新帖子' : '添加宠物'}
                </ThemedText>
              </View>
            </Pressable>
          ))}
        </View>
        <View style={styles.headerSpace} />
      </View>

      {!user ? (
        <View style={styles.center}>
          <ThemedText style={styles.centerEmoji}>🔐</ThemedText>
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
    <ScrollView contentContainerStyle={[styles.form, { paddingBottom: Spacing.five }]}>
      <Pressable
        onPress={pickImage}
        style={[styles.imagePicker, { borderColor: theme.backgroundSelected }]}
      >
        {image ? (
          <Image
            source={{ uri: image.uri }}
            style={{ width: '100%', height: '100%' }}
            contentFit="cover"
          />
        ) : (
          <View style={styles.imagePickerEmpty}>
            <AppIcon name="image-multiple" size={34} color={theme.textSecondary} />
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
          {pets.map((pet) => (
            <Pressable
              key={pet.id}
              onPress={() => setPetId(pet.id === petId ? null : pet.id)}
              style={[styles.petChip, petId === pet.id && { backgroundColor: theme.tint }]}
            >
              <ThemedText type="small" style={petId === pet.id ? { color: '#ffffff' } : undefined}>
                {pet.avatar_emoji} {pet.name}
              </ThemedText>
            </Pressable>
          ))}
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
        <AppIcon name="map-marker-radius" size={16} color={theme.tint} />
        <TextInput
          value={location}
          onChangeText={setLocation}
          placeholder="添加位置（可选）"
          placeholderTextColor={theme.textSecondary}
          maxLength={80}
          style={[styles.locationInput, { color: theme.text }]}
        />
      </View>

      {error && (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      )}

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
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await createPet({
        name: name.trim(),
        species,
        breed: breed.trim(),
        avatarEmoji,
        deviceId: deviceId.trim(),
      });
      router.back();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '添加失败，请重试');
      setSending(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={[styles.form, { paddingBottom: Spacing.five }]}>
      <View style={styles.emojiRow}>
        {PET_EMOJIS.map((emoji) => (
          <Pressable
            key={emoji}
            onPress={() => setAvatarEmoji(emoji)}
            style={[styles.emojiCell, avatarEmoji === emoji && { borderColor: theme.tint }]}
          >
            <ThemedText style={{ fontSize: 24 }}>{emoji}</ThemedText>
          </Pressable>
        ))}
      </View>

      <TextInput
        value={name}
        onChangeText={setName}
        placeholder="名字（必填）"
        placeholderTextColor={theme.textSecondary}
        maxLength={20}
        style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />

      <View style={styles.speciesRow}>
        {SPECIES.map((item) => (
          <Pressable
            key={item.id}
            onPress={() => setSpecies(item.id)}
            style={[styles.speciesChip, species === item.id && { backgroundColor: theme.tint }]}
          >
            <ThemedText type="small" style={species === item.id ? { color: '#ffffff' } : undefined}>
              {item.emoji} {item.label}
            </ThemedText>
          </Pressable>
        ))}
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
        placeholder="项圈设备 ID，如 esp32-001（可选）"
        placeholderTextColor={theme.textSecondary}
        maxLength={40}
        autoCapitalize="none"
        style={[styles.field, { color: theme.text, borderColor: theme.backgroundSelected }]}
      />

      {error && (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      )}

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
          <ActivityIndicator color="#ffffff" />
        ) : (
          <ThemedText
            type="smallBold"
            style={{ color: name.trim() ? '#ffffff' : theme.textSecondary }}
          >
            添加宠物
          </ThemedText>
        )}
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  sheet: {
    flex: 1,
    alignItems: 'center',
  },
  header: {
    width: '100%',
    maxWidth: MaxContentWidth,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.three,
  },
  headerSpace: { width: 26 },
  segment: {
    flexDirection: 'row',
    borderRadius: 14,
    padding: 3,
  },
  segmentItem: {
    borderRadius: 11,
    paddingHorizontal: Spacing.three,
    paddingVertical: 6,
  },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: Spacing.two },
  centerEmoji: { fontSize: 40 },
  form: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    gap: Spacing.three,
  },
  imagePicker: {
    height: 240,
    borderRadius: 18,
    borderWidth: 1.5,
    borderStyle: 'dashed',
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  imagePickerEmpty: { alignItems: 'center', gap: Spacing.two },
  petRow: { gap: Spacing.two },
  petChip: {
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 6,
    backgroundColor: 'rgba(148, 131, 111, 0.12)',
  },
  captionInput: {
    borderWidth: 1,
    borderRadius: 14,
    padding: Spacing.three,
    minHeight: 96,
    textAlignVertical: 'top',
    fontSize: 14,
  },
  counter: { textAlign: 'right', marginTop: -Spacing.two },
  locationRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
  },
  locationInput: { flex: 1, paddingVertical: 10, fontSize: 14 },
  primaryButton: {
    borderRadius: 16,
    alignItems: 'center',
    paddingVertical: 12,
    marginTop: Spacing.one,
  },
  emojiRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  emojiCell: {
    width: 52,
    height: 52,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: 'transparent',
    backgroundColor: 'rgba(148, 131, 111, 0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  speciesRow: { flexDirection: 'row', gap: Spacing.two },
  speciesChip: {
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 8,
    backgroundColor: 'rgba(148, 131, 111, 0.12)',
  },
  field: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
    fontSize: 14,
  },
});
