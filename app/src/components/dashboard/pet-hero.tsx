import { Image } from 'expo-image';
import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Fonts, Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import {
  PET_ACTIONS,
  selectPetAvatar,
  shouldPromotePetMedia,
  type PetActionKey,
} from '@/services/pet-avatar';
import type { PetAvatarTemplate, PetgifPet, SocialPet } from '@/types/social';
import type { TelemetryFrame } from '@/types/telemetry';

/** 数字宠物即界面：轻点播放互动，长按编辑；不设说明性按钮。 */
export function PetHero({
  pet,
  petgif,
  avatarTemplate,
  frame,
  current,
  statusLabel,
  refreshVersion,
  onEdit,
}: {
  pet: SocialPet;
  petgif: PetgifPet | null | undefined;
  avatarTemplate?: PetAvatarTemplate | null;
  frame: TelemetryFrame | null;
  current: boolean;
  statusLabel: string;
  refreshVersion: number;
  onEdit: () => void;
}) {
  const theme = useTheme();
  const [previewAction, setPreviewAction] = useState<PetActionKey | null>(null);
  const [interactionAction, setInteractionAction] = useState<PetActionKey | null>(null);
  const [interacted, setInteracted] = useState(false);
  useEffect(() => {
    if (!interactionAction) return;
    const timer = setTimeout(() => setInteractionAction(null), 4_000);
    return () => clearTimeout(timer);
  }, [interactionAction]);
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
    interactionAction,
    failedUrls: failed,
  });
  const animation = media.animationUrl;
  const still = media.staticUrl;
  const uri = animation || still;
  const color = current ? theme.online : theme.textSecondary;
  const actionLabel =
    media.label ??
    (current
      ? frame?.edge?.action?.label === 'warming_up'
        ? '识别准备中'
        : '暂未识别动作'
      : statusLabel);
  const displayLabel = `${media.isPreview ? '动作预览 · ' : ''}${actionLabel}${
    media.animationState === 'barking' ? ' · 吠叫' : ''
  }`;
  const playInteraction = () => {
    if (media.actionKey === null || interactionAction === media.actionKey) return;
    setInteracted(true);
    setInteractionAction(media.actionKey);
  };
  const [displayedMedia, setDisplayedMedia] = useState(
    uri ? { uri, animation: Boolean(animation) } : null,
  );
  const requestedUri = useRef(uri);
  useEffect(() => {
    requestedUri.current = uri;
  }, [uri]);
  const visibleMedia = uri ? displayedMedia : null;
  const markFailed = (failedUri: string) =>
    setFailures((old) => ({
      petId: pet.id,
      refreshVersion,
      urls: [
        ...(old.petId === pet.id && old.refreshVersion === refreshVersion ? old.urls : []),
        failedUri,
      ],
    }));
  const waitingForMedia = Boolean(uri && visibleMedia?.uri !== uri);
  return (
    <View style={styles.hero}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${pet.name}的${animation ? `${displayLabel}动画` : '头像'}${
          canPreview
            ? '，轻点切换预设动作，长按编辑宠物档案'
            : media.interactionAvailable
              ? '，轻点播放互动动画，长按编辑宠物档案'
              : '，长按编辑宠物档案'
        }`}
        onLongPress={onEdit}
        onPress={() => {
          if (canPreview) {
            setInteractionAction(null);
            setPreviewAction((previous) => {
              const currentIndex = previous ? previewActions.indexOf(previous) : -1;
              return previewActions[(currentIndex + 1) % previewActions.length];
            });
            return;
          }
          if (media.interactionAvailable) {
            playInteraction();
            return;
          }
          onEdit();
        }}
        style={[styles.ring, { borderColor: color, backgroundColor: theme.tintBackground }]}
      >
        <View style={styles.portrait}>
          {visibleMedia ? (
            <Image
              source={{ uri: absoluteUrl(visibleMedia.uri) }}
              contentFit={visibleMedia.animation ? 'contain' : 'cover'}
              autoplay={visibleMedia.animation}
              cachePolicy="memory-disk"
              transition={180}
              recyclingKey={String(pet.id)}
              style={styles.image}
              onError={() => markFailed(visibleMedia.uri)}
            />
          ) : (
            <ThemedText style={styles.emoji}>{pet.avatar_emoji || '🐾'}</ThemedText>
          )}
          {waitingForMedia && uri && (
            <Image
              key={`preload:${pet.id}:${uri}`}
              source={{ uri: absoluteUrl(uri) }}
              contentFit={animation ? 'contain' : 'cover'}
              autoplay={!!animation}
              cachePolicy="memory-disk"
              recyclingKey={`preload:${pet.id}:${uri}`}
              style={styles.preload}
              onLoad={() => {
                if (shouldPromotePetMedia(uri, requestedUri.current)) {
                  setDisplayedMedia({ uri, animation: Boolean(animation) });
                }
              }}
              onError={() => markFailed(uri)}
            />
          )}
        </View>
        <View
          style={[
            styles.badge,
            { backgroundColor: theme.backgroundElement, boxShadow: Shadows.chip },
          ]}
        >
          <View style={[styles.dot, { backgroundColor: color }]} />
          <ThemedText type="smallBold">{displayLabel}</ThemedText>
        </View>
      </Pressable>
      <View style={styles.caption}>
        {current && media.interactionAvailable && !interacted && (
          <ThemedText type="small" themeColor="textSecondary">
            轻点它，跟它互动
          </ThemedText>
        )}
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
  hero: { alignItems: 'center', gap: 12, paddingTop: 4, paddingBottom: 4 },
  ring: {
    width: 180,
    height: 180,
    borderRadius: 90,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  portrait: {
    width: 166,
    height: 166,
    borderRadius: 83,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: { width: '100%', height: '100%' },
  preload: { position: 'absolute', width: 1, height: 1, opacity: 0 },
  emoji: { fontSize: 62, lineHeight: 80, fontFamily: Fonts.rounded },
  badge: {
    position: 'absolute',
    bottom: -10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 7,
    maxWidth: 240,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  caption: { alignItems: 'center', gap: 6, minHeight: 20, paddingTop: 8 },
});
