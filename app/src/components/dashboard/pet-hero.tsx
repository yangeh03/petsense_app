import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Fonts, Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import { PET_ACTIONS, selectPetAvatar, type PetActionKey } from '@/services/pet-avatar';
import { soundStatus } from '@/services/sound-view';
import type { PetAvatarTemplate, PetgifPet, SocialPet } from '@/types/social';
import type { TelemetryFrame } from '@/types/telemetry';

/** Central digital pet. Exact action asset or static avatar; never a different action. */
export function PetHero({
  pet,
  petgif,
  avatarTemplate,
  frame,
  current,
  statusLabel,
  refreshVersion,
  onEdit,
  onSoundPress,
}: {
  pet: SocialPet;
  petgif: PetgifPet | null | undefined;
  avatarTemplate?: PetAvatarTemplate | null;
  frame: TelemetryFrame | null;
  current: boolean;
  statusLabel: string;
  refreshVersion: number;
  onEdit: () => void;
  onSoundPress: () => void;
}) {
  const theme = useTheme();
  const [previewAction, setPreviewAction] = useState<PetActionKey | null>(null);
  // Retry failed media on pull-to-refresh or the explicit retry button; keep good GIFs mounted.
  const [failures, setFailures] = useState<{
    petId: number;
    refreshVersion: number;
    urls: string[];
  }>({
    petId: pet.id,
    refreshVersion,
    urls: [],
  });
  const failed =
    failures.petId === pet.id && failures.refreshVersion === refreshVersion ? failures.urls : [];
  const previewActions = (Object.keys(PET_ACTIONS) as PetActionKey[]).filter((action) =>
    Boolean(avatarTemplate?.behaviors[action]?.gifUrl),
  );
  const canPreview = !current && previewActions.length > 0;
  const media = selectPetAvatar({
    pet,
    petgif,
    avatarTemplate,
    frame,
    fresh: current,
    previewAction: canPreview ? previewAction : null,
    failedUrls: failed,
  });
  const animation = media.animationUrl;
  const still = media.staticUrl;
  const uri = animation || still;
  const color = current ? theme.online : theme.textSecondary;
  const sound = soundStatus(frame, current, !!pet.device_id);
  const actionLabel =
    media.label ??
    (current
      ? frame?.edge?.action?.label === 'warming_up'
        ? '识别准备中'
        : '暂未识别动作'
      : statusLabel);
  return (
    <View style={styles.hero}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${pet.name}的${animation ? `${actionLabel}动画` : '头像'}${
          canPreview ? '，轻点切换预设动作，长按编辑宠物档案' : '，编辑宠物档案'
        }`}
        onLongPress={canPreview ? onEdit : undefined}
        onPress={() => {
          if (!canPreview) {
            onEdit();
            return;
          }
          setPreviewAction((previous) => {
            const currentIndex = previous ? previewActions.indexOf(previous) : -1;
            return previewActions[(currentIndex + 1) % previewActions.length];
          });
        }}
        style={[styles.ring, { borderColor: color, backgroundColor: theme.tintBackground }]}
      >
        <View style={[styles.portrait, { borderColor: theme.backgroundElement }]}>
          {uri ? (
            <Image
              key={`${pet.id}:${uri}`}
              source={{ uri: absoluteUrl(uri) }}
              contentFit={animation ? 'contain' : 'cover'}
              autoplay={!!animation}
              cachePolicy="memory-disk"
              transition={120}
              recyclingKey={`${pet.id}:${uri}`}
              style={styles.image}
              onError={() =>
                setFailures((old) => ({
                  petId: pet.id,
                  refreshVersion,
                  urls: [
                    ...(old.petId === pet.id && old.refreshVersion === refreshVersion
                      ? old.urls
                      : []),
                    uri,
                  ],
                }))
              }
            />
          ) : (
            <ThemedText style={styles.emoji}>{pet.avatar_emoji || '🐾'}</ThemedText>
          )}
        </View>
        <View
          style={[
            styles.badge,
            { backgroundColor: theme.backgroundElement, boxShadow: Shadows.chip },
          ]}
        >
          <View style={[styles.dot, { backgroundColor: color }]} />
          <ThemedText type="smallBold">{actionLabel}</ThemedText>
        </View>
      </Pressable>
      <View style={styles.caption}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${sound.label}，查看声音诊断`}
          onPress={onSoundPress}
          style={[
            styles.sound,
            { backgroundColor: sound.available ? theme.tintBackground : theme.backgroundElement },
          ]}
        >
          <SymbolView
            name={{ ios: 'waveform', android: 'graphic_eq', web: 'graphic_eq' }}
            size={16}
            tintColor={sound.available ? theme.tint : theme.textSecondary}
          />
          <ThemedText
            type="small"
            style={{ color: sound.available ? theme.tint : theme.textSecondary }}
          >
            {sound.label}
          </ThemedText>
        </Pressable>
        {failed.length > 0 && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="重试宠物形象"
            hitSlop={8}
            onPress={() => setFailures({ petId: pet.id, refreshVersion, urls: [] })}
          >
            <ThemedText type="small" style={{ color: theme.tint }}>
              重试形象
            </ThemedText>
          </Pressable>
        )}
      </View>
    </View>
  );
}
const styles = StyleSheet.create({
  hero: { alignItems: 'center', gap: 18, paddingTop: 4, paddingBottom: 12 },
  ring: {
    width: 176,
    height: 176,
    borderRadius: 88,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  portrait: {
    width: 164,
    height: 164,
    borderRadius: 82,
    borderWidth: 5,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: { width: '100%', height: '100%' },
  emoji: { fontSize: 78, lineHeight: 98, fontFamily: Fonts.rounded },
  badge: {
    position: 'absolute',
    bottom: -10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 7,
    maxWidth: 260,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  caption: { alignItems: 'center', gap: 6, paddingTop: 4 },
  sound: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 5,
  },
});
