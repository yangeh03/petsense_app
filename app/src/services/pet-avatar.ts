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

/** A loaded image may replace the current visual only if it is still the requested action. */
export function shouldPromotePetMedia(loadedUri: string, requestedUri: string | null) {
  return loadedUri === requestedUri;
}

export function selectPetAvatar({
  pet,
  petgif,
  avatarTemplate,
  frame,
  fresh,
  previewAction,
  interactionAction,
  failedUrls = [],
}: {
  pet: SocialPet;
  petgif: PetgifPet | null | undefined;
  avatarTemplate?: PetAvatarTemplate | null;
  frame: TelemetryFrame | null;
  fresh: boolean;
  previewAction?: PetActionKey | null;
  interactionAction?: PetActionKey | null;
  failedUrls?: readonly string[];
}) {
  const ownAssets = pet.petgif_id && petgif?.id === pet.petgif_id ? petgif : null;
  const selectedTemplate = pet.avatar_template_id === avatarTemplate?.id ? avatarTemplate : null;
  const matching = Boolean(fresh && pet.device_id && frame?.deviceId === pet.device_id);
  const liveActionKey = matching ? inferPetAction(frame) : null;
  const actionKey =
    liveActionKey || (!fresh && selectedTemplate && previewAction ? previewAction : null);
  const actionCandidates = actionKey
    ? [ownAssets?.behaviors[actionKey], selectedTemplate?.behaviors[actionKey]]
    : [];
  // State animations must stay with the character supplying the ordinary action.
  const behavior = actionCandidates.find(
    (item) => item?.gifUrl && !failedUrls.includes(item.gifUrl),
  );
  const sound = matching ? frame?.edge?.sound : null;
  const barking = sound?.label === 'bark' && confident(sound.confidence);
  const requestedState = barking
    ? 'barking'
    : actionKey && interactionAction === actionKey
      ? 'interaction'
      : null;
  const stateUrl = requestedState ? behavior?.states?.[requestedState]?.gifUrl : null;
  const stateAvailable = Boolean(stateUrl && !failedUrls.includes(stateUrl));
  const interactionUrl = behavior?.states?.interaction?.gifUrl;
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
    animationAvailable: actionCandidates.some((item) => Boolean(item?.gifUrl)),
    animationUrl: (stateAvailable ? stateUrl : behavior?.gifUrl) || null,
    animationState: stateAvailable ? requestedState : 'normal',
    interactionAvailable: Boolean(
      interactionUrl && !failedUrls.includes(interactionUrl) && !barking,
    ),
    isPreview: Boolean(actionKey && !liveActionKey),
    staticUrl: staticCandidates.find((url) => url && !failedUrls.includes(url)) || null,
  };
}
