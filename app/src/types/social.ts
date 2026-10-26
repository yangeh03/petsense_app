/** App 社区与账号相关类型，对齐后端 /api/app/* 返回结构 */

export type AppUser = {
  id: number;
  username: string;
  displayName: string;
  avatarEmoji: string;
};

export type PetgifBehavior = {
  status?: string;
  gifUrl?: string | null;
  videoUrl?: string | null;
  states?: Record<string, { gifUrl?: string | null }>;
};

export type PetgifPet = {
  id: string;
  name?: string | null;
  status?: string;
  stage?: string;
  profile: {
    name?: string | null;
    breed?: string | null;
    traits?: string[];
    summary?: string | null;
  };
  characterCardUrl?: string | null;
  behaviors: Record<string, PetgifBehavior>;
  diaries: {
    id?: string;
    date?: string | null;
    durationMin?: number | null;
    steps?: number | null;
    posterUrl?: string | null;
  }[];
};

export type PetAvatarTemplate = {
  id: string;
  name: string;
  previewUrl?: string | null;
  characterCardUrl?: string | null;
  behaviors: Record<string, PetgifBehavior>;
};

export type SocialPet = {
  id: number;
  user_id: number;
  name: string;
  species: string;
  breed: string;
  avatar_emoji: string;
  device_id: string;
  created_at: string;
  petgif_id?: string | null;
  avatar_template_id?: string | null;
  avatar_image?: string | null;
  avatarImageUrl?: string | null;
  batteryPercent?: number | null;
  online?: boolean;
};

export type PostAuthor = {
  user_id: number;
  display_name: string;
  avatar_emoji: string;
};

export type PostMediaKind = 'image' | 'gif' | 'video';

export type Post = PostAuthor & {
  id: number;
  caption: string;
  location_text: string;
  imageUrl: string;
  mediaUrl?: string | null;
  mediaKind?: PostMediaKind | null;
  created_at: string;
  pet_id: number | null;
  pet_name: string | null;
  pet_emoji: string | null;
  likes: number;
  comments_count: number;
  liked_by_me: boolean;
  isMine: boolean;
};

export type PostComment = {
  id: number;
  body: string;
  created_at: string;
  user_id: number;
  display_name: string;
  avatar_emoji: string;
};

export type UserProfile = AppUser & {
  bio: string;
  posts_count: number;
  followers: number;
  following: number;
  is_following: boolean;
  pets: SocialPet[];
};

export type DashboardTelemetry = {
  heartRate: number;
  spo2: number;
  bodyTemp: number;
  envTemp: number;
  respiration: number;
  hrvSdnn: number;
  microCir: number;
  fatigue: number;
  systolic: number;
  diastolic: number;
  valid: boolean;
  batteryPercent: number;
  online: boolean;
  receivedAt: string;
};

export type Insight = {
  icon: string;
  text: string;
};

/** 无宠物时接口只返回 { pet: null }，不包含统计字段。 */
export type DashboardData =
  | DashboardWithPet
  | {
      pet: null;
      latest?: never;
      today?: never;
      insight?: never;
      petgif?: never;
      avatarTemplate?: never;
    };

type DashboardWithPet = {
  pet: SocialPet;
  latest: DashboardTelemetry | null;
  today: {
    sleepMinutes: number;
    activeMinutes: number;
    avgHeartRate: number;
  };
  insight: Insight;
  petgif?: PetgifPet | null;
  avatarTemplate?: PetAvatarTemplate | null;
};

/** 趋势统计周期 */
export type TrendPeriod = 'day' | 'week' | 'month' | 'all';

export type TrendMetric = 'heartRate' | 'spo2' | 'bodyTemp';
export type TrendStats = {
  count: number;
  avg: number | null;
  min: number | null;
  max: number | null;
};
export type TrendBucket = {
  date: string;
  label: string;
  start: string;
  end: string;
  metrics: Record<TrendMetric, TrendStats>;
  avgHr: number | null;
  minHr: number | null;
  maxHr: number | null;
  avgSpo2: number | null;
  avgTemp: number | null;
  samples: number;
};
export type TrendsResponse = {
  success: boolean;
  schemaVersion: number;
  period: TrendPeriod;
  generatedAt: string;
  window: { start: string; end: string; granularity: 'hour' | 'day' };
  actualRange: { start: string; end: string } | null;
  summary: Record<TrendMetric, TrendStats>;
  coverage: Record<TrendMetric, { recordedBuckets: number; totalBuckets: number }>;
  buckets: TrendBucket[];
};

/** 关注/粉丝列表行 */
export type FollowListUser = {
  id: number;
  display_name: string;
  avatar_emoji: string;
  is_following: boolean;
};
