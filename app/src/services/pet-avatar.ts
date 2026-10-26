import type { PetAvatarTemplate, PetgifPet, SocialPet } from '@/types/social';
import type { TelemetryFrame } from '@/types/telemetry';

export const PET_ACTIONS = {
  stand: '站立',
  sit: '坐着',
  lie_down: '趴着',
  walk: '走动',
  run: '奔跑',
  sniff: '嗅闻',
  jump: '跳跃',
  eat: '进食',
  sleep: '睡觉',
} as const;
export type PetActionKey = keyof typeof PET_ACTIONS;
const EDGE_ACTIONS: Record<string, PetActionKey> = {
  standing: 'stand',
  sitting: 'sit',
  'lying down': 'lie_down',
  walking: 'walk',
};
const confident = (value: number) => Number.isFinite(value) && value >= 0.6 && value <= 1;

/** Follow reported model results only. No IMU, clock or arbitrary idle-action guesses. */
export function inferPetAction(frame: TelemetryFrame | null | undefined): PetActionKey | null {
  if (frame?.edge) {
    const prediction = frame.edge.action;
    return prediction && confident(prediction.confidence)
      ? (EDGE_ACTIONS[prediction.label] ?? null)
      : null;
  }
  const prediction = frame?.action;
  return prediction &&
    confident(prediction.confidence) &&
    Object.hasOwn(PET_ACTIONS, prediction.actionKey)
    ? (prediction.actionKey as PetActionKey)
    : null;
}

export function selectPetAvatar({
  pet,
  petgif,
  avatarTemplate,
  frame,
  fresh,
  previewAction,
  failedUrls = [],
}: {
  pet: SocialPet;
  petgif: PetgifPet | null | undefined;
  avatarTemplate?: PetAvatarTemplate | null;
  frame: TelemetryFrame | null;
  fresh: boolean;
  previewAction?: PetActionKey | null;
  failedUrls?: readonly string[];
}) {
  const ownAssets = pet.petgif_id && petgif?.id === pet.petgif_id ? petgif : null;
  const selectedTemplate = pet.avatar_template_id === avatarTemplate?.id ? avatarTemplate : null;
  const liveActionKey =
    fresh && pet.device_id && frame?.deviceId === pet.device_id ? inferPetAction(frame) : null;
  const actionKey =
    liveActionKey || (!fresh && selectedTemplate && previewAction ? previewAction : null);
  const actionCandidates = actionKey
    ? [ownAssets?.behaviors[actionKey]?.gifUrl, selectedTemplate?.behaviors[actionKey]?.gifUrl]
    : [];
  const preset = actionCandidates.find((url) => url && !failedUrls.includes(url)) || null;
  const staticCandidates = [
    // A selected preset is an explicit home-screen representation while no live action is available.
    ...(!actionKey && selectedTemplate ? [selectedTemplate.characterCardUrl] : []),
    pet.avatarImageUrl,
    ownAssets?.characterCardUrl,
    selectedTemplate?.characterCardUrl,
  ];
  return {
    actionKey,
    label: actionKey ? PET_ACTIONS[actionKey] : selectedTemplate ? '静止站立' : null,
    animationAvailable: actionCandidates.some(Boolean),
    animationUrl: preset,
    staticUrl: staticCandidates.find((url) => url && !failedUrls.includes(url)) || null,
  };
}
