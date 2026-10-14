/** App 社区与账号相关类型，对齐后端 /api/app/* 返回结构 */

export type AppUser = {
  id: number;
  username: string;
  displayName: string;
  avatarEmoji: string;
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
  batteryPercent?: number | null;
  online?: boolean;
};

export type PostAuthor = {
  user_id: number;
  display_name: string;
  avatar_emoji: string;
};

export type Post = PostAuthor & {
  id: number;
  caption: string;
  location_text: string;
  imageUrl: string;
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

export type DashboardData = {
  pet: SocialPet | null;
  latest: DashboardTelemetry | null;
  today: {
    sleepMinutes: number;
    activeMinutes: number;
    avgHeartRate: number;
  };
  insight: Insight;
};

/** 趋势统计周期 */
export type TrendPeriod = 'day' | 'week' | 'month' | 'all';

/** 趋势统计桶：按小时/按天聚合的平均体征值 */
export type TrendBucket = {
  date: string;
  label: string;
  avgHr: number | null;
  minHr: number | null;
  maxHr: number | null;
  avgSpo2: number | null;
  avgTemp: number | null;
  samples: number;
};

export type TrendsResponse = {
  success: boolean;
  period: string;
  buckets: TrendBucket[];
};

/** 关注/粉丝列表行 */
export type FollowListUser = {
  id: number;
  display_name: string;
  avatar_emoji: string;
  is_following: boolean;
};
