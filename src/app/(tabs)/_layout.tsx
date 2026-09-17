import * as SplashScreen from 'expo-splash-screen';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import AppTabs from '@/components/app-tabs';
import { CurrentPetProvider } from '@/context/current-pet';

SplashScreen.preventAutoHideAsync();

export default function TabLayout() {
  return (
    <CurrentPetProvider>
      <AnimatedSplashOverlay />
      <AppTabs />
    </CurrentPetProvider>
  );
}
