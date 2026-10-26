import { Image } from 'expo-image';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import { PET_AVATARS, PET_SPECIES, type PetDraft, type PetErrors } from '@/services/pet-profile';
import type { SocialPet } from '@/types/social';

export function PetProfileForm({
  pet,
  draft,
  errors,
  disabled,
  picking,
  onChange,
  onPickPhoto,
}: {
  pet: SocialPet;
  draft: PetDraft;
  errors: PetErrors;
  disabled: boolean;
  picking: boolean;
  onChange: (patch: Partial<PetDraft>) => void;
  onPickPhoto: () => void;
}) {
  const theme = useTheme();
  const photoUri =
    draft.avatarMode === 'photo'
      ? draft.photo?.uri
      : draft.avatarMode === 'keep' && pet.avatarImageUrl
        ? absoluteUrl(pet.avatarImageUrl)
        : null;
  const inputStyle = [
    styles.input,
    { color: theme.text, backgroundColor: theme.background, borderColor: theme.backgroundSelected },
  ];
  return (
    <>
      <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
        <ThemedText type="smallBold">宠物头像</ThemedText>
        <View style={styles.avatarRow}>
          <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
            {photoUri ? (
              <Image source={{ uri: photoUri }} style={styles.photo} contentFit="cover" />
            ) : (
              <ThemedText style={styles.emoji}>{draft.avatarEmoji}</ThemedText>
            )}
          </View>
          <View style={styles.avatarActions}>
            <Pressable
              accessibilityRole="button"
              disabled={disabled || picking}
              onPress={onPickPhoto}
              style={[styles.smallButton, { backgroundColor: theme.tintBackground }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                {picking ? '正在选择…' : '选择照片'}
              </ThemedText>
            </Pressable>
            <ThemedText type="small" themeColor="textSecondary">
              JPG、PNG 或 WebP，最大 10MB
            </ThemedText>
          </View>
        </View>
        {draft.avatarMode !== 'keep' && (
          <Pressable
            accessibilityRole="button"
            disabled={disabled}
            onPress={() =>
              onChange({ avatarMode: 'keep', photo: null, avatarEmoji: pet.avatar_emoji })
            }
          >
            <ThemedText type="small" style={{ color: theme.tint }}>
              恢复已保存头像
            </ThemedText>
          </Pressable>
        )}
        <ThemedText type="small" themeColor="textSecondary">
          或使用默认头像
        </ThemedText>
        <View style={styles.chips}>
          {PET_AVATARS.map((emoji) => {
            const selected = draft.avatarMode === 'emoji' && draft.avatarEmoji === emoji;
            return (
              <Pressable
                key={emoji}
                disabled={disabled}
                accessibilityRole="button"
                accessibilityLabel={`使用默认头像 ${emoji}`}
                accessibilityState={{ selected }}
                onPress={() => onChange({ avatarMode: 'emoji', avatarEmoji: emoji, photo: null })}
                style={[
                  styles.emojiChip,
                  {
                    borderColor: selected ? theme.tint : theme.backgroundSelected,
                    backgroundColor: selected ? theme.tintBackground : theme.background,
                  },
                ]}
              >
                <ThemedText style={{ fontSize: 25 }}>{emoji}</ThemedText>
              </Pressable>
            );
          })}
        </View>
        <ThemedText type="small" themeColor="textSecondary">
          默认头像会替换照片；首页已有的数字形象仍可能展示。修改会在保存后生效。
        </ThemedText>
        {!!errors.avatar && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {errors.avatar}
          </ThemedText>
        )}
      </View>

      <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
        <View style={styles.labelRow}>
          <ThemedText type="smallBold">
            名字 <ThemedText style={{ color: theme.danger }}>*</ThemedText>
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {Array.from(draft.name.trim()).length}/20
          </ThemedText>
        </View>
        <TextInput
          value={draft.name}
          onChangeText={(name) => onChange({ name })}
          editable={!disabled}
          accessibilityLabel="宠物名字"
          placeholder="它叫什么名字？"
          placeholderTextColor={theme.textSecondary}
          returnKeyType="done"
          style={inputStyle}
        />
        {!!errors.name && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {errors.name}
          </ThemedText>
        )}
        <ThemedText type="smallBold">物种</ThemedText>
        <View style={styles.chips}>
          {PET_SPECIES.map((item) => {
            const selected = draft.species === item.id;
            return (
              <Pressable
                key={item.id}
                disabled={disabled}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => {
                  const previousDefault = PET_SPECIES.find(
                    (entry) => entry.id === draft.species,
                  )?.emoji;
                  onChange({
                    species: item.id,
                    ...(item.id !== 'dog' ? { avatarTemplateId: null } : {}),
                    ...(draft.avatarEmoji === previousDefault ? { avatarEmoji: item.emoji } : {}),
                  });
                }}
                style={[
                  styles.speciesChip,
                  {
                    borderColor: selected ? theme.tint : theme.backgroundSelected,
                    backgroundColor: selected ? theme.tintBackground : theme.background,
                  },
                ]}
              >
                <ThemedText type="smallBold">
                  {item.emoji} {item.label}
                </ThemedText>
              </Pressable>
            );
          })}
        </View>
        {!!errors.species && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {errors.species}
          </ThemedText>
        )}
        <View style={styles.labelRow}>
          <ThemedText type="smallBold">
            品种{' '}
            <ThemedText type="small" themeColor="textSecondary">
              （选填）
            </ThemedText>
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {Array.from(draft.breed.trim()).length}/40
          </ThemedText>
        </View>
        <TextInput
          value={draft.breed}
          onChangeText={(breed) => onChange({ breed })}
          editable={!disabled}
          accessibilityLabel="宠物品种"
          placeholder="例如：中华田园犬"
          placeholderTextColor={theme.textSecondary}
          returnKeyType="done"
          style={inputStyle}
        />
        {!!errors.breed && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {errors.breed}
          </ThemedText>
        )}
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  card: { padding: Spacing.four, borderRadius: 24, gap: Spacing.three },
  avatarRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three, flexWrap: 'wrap' },
  avatar: {
    width: 88,
    height: 88,
    borderRadius: 44,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  emoji: { fontSize: 42, lineHeight: 55 },
  photo: { width: '100%', height: '100%' },
  avatarActions: { gap: Spacing.two, flexShrink: 1 },
  smallButton: {
    alignSelf: 'flex-start',
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 12,
    minHeight: 44,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  emojiChip: {
    minWidth: 52,
    minHeight: 52,
    borderWidth: 1.5,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  speciesChip: { borderWidth: 1.5, borderRadius: 14, padding: 12, minHeight: 48 },
  labelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  input: { borderWidth: 1, borderRadius: 14, padding: Spacing.three, fontSize: 16, minHeight: 52 },
});
