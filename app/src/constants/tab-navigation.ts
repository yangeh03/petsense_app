/** Shared tab structure keeps native and web navigation aligned. */
export const APP_TABS = [
  {
    name: 'index',
    href: '/',
    label: '今日',
    icon: { ios: 'house', android: 'home', web: 'home' },
    selectedIcon: { ios: 'house.fill', android: 'home_filled', web: 'home' },
  },
  {
    name: 'records',
    href: '/records',
    label: '记录',
    icon: { ios: 'chart.line.uptrend.xyaxis', android: 'monitoring', web: 'monitoring' },
    selectedIcon: { ios: 'chart.line.uptrend.xyaxis', android: 'monitoring', web: 'monitoring' },
  },
  {
    name: 'tracking',
    href: '/tracking',
    label: '位置',
    icon: { ios: 'mappin.and.ellipse', android: 'place', web: 'place' },
    selectedIcon: { ios: 'mappin.and.ellipse.circle', android: 'place', web: 'place' },
  },
  {
    name: 'community',
    href: '/community',
    label: '社区',
    icon: { ios: 'person.2', android: 'people_outline', web: 'people_outline' },
    selectedIcon: { ios: 'person.2.fill', android: 'people', web: 'people' },
  },
  {
    name: 'profile',
    href: '/profile',
    label: '我的',
    icon: { ios: 'person', android: 'person_outline', web: 'person_outline' },
    selectedIcon: { ios: 'person.fill', android: 'person', web: 'person' },
  },
] as const;
