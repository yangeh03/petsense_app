/**
 * PetSense 演示用的 Mock 数据。
 * 后续接入真实后端时，将本文件替换为 API 请求层即可（类型保持不变）。
 */

export type PetSpecies = 'dog' | 'cat' | 'rabbit';

export type Pet = {
  id: string;
  name: string;
  species: PetSpecies;
  breed: string;
  /** 年龄（岁） */
  age: number;
  /** 体重（kg） */
  weight: number;
  /** 疫苗是否齐全 */
  vaccinated: boolean;
  /** 头像使用的 emoji，避免引入图片资源 */
  emoji: string;
  /** 一句话简介 */
  bio: string;
};

export type ActivityMetric = {
  id: string;
  /** 指标名称，如“今日步数” */
  label: string;
  /** 当前值 */
  value: number;
  /** 目标值，用于计算进度 */
  goal: number;
  /** 数值单位 */
  unit: string;
  /** 数值展示图标（SF Symbols / Material 名称由组件映射） */
  icon: 'footsteps' | 'clock' | 'drop';
};

export type CareTask = {
  id: string;
  title: string;
  time: string;
  done: boolean;
};

export const pets: Pet[] = [
  {
    id: 'pet-001',
    name: '豆豆',
    species: 'dog',
    breed: '柯基',
    age: 2,
    weight: 11.5,
    vaccinated: true,
    emoji: '🐶',
    bio: '精力旺盛的短腿小柯基，最爱追飞盘。',
  },
  {
    id: 'pet-002',
    name: '年糕',
    species: 'cat',
    breed: '英短蓝猫',
    age: 3,
    weight: 4.8,
    vaccinated: true,
    emoji: '🐱',
    bio: '高冷但贪吃，最近在减肥的路上越走越远。',
  },
  {
    id: 'pet-003',
    name: '雪球',
    species: 'rabbit',
    breed: '垂耳兔',
    age: 1,
    weight: 2.2,
    vaccinated: false,
    emoji: '🐰',
    bio: '安静的小可爱，喜欢吃提摩西草。',
  },
];

export const activityMetrics: ActivityMetric[] = [
  { id: 'steps', label: '今日运动', value: 6200, goal: 8000, unit: '步', icon: 'footsteps' },
  { id: 'active', label: '活跃时长', value: 96, goal: 120, unit: '分钟', icon: 'clock' },
  { id: 'water', label: '饮水量', value: 320, goal: 400, unit: '毫升', icon: 'drop' },
];

export const careTasks: CareTask[] = [
  { id: 'task-001', title: '早餐喂食', time: '08:00', done: true },
  { id: 'task-002', title: '户外散步 30 分钟', time: '18:30', done: false },
  { id: 'task-003', title: '梳毛护理', time: '20:00', done: false },
  { id: 'task-004', title: '体重记录', time: '21:00', done: false },
];

export const speciesLabel: Record<PetSpecies, string> = {
  dog: '狗狗',
  cat: '猫咪',
  rabbit: '兔子',
};
