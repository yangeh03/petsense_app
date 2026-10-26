import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useColorScheme } from 'react-native';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import { AuthProvider } from '@/hooks/use-auth';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();
  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <AuthProvider>
        <AnimatedSplashOverlay />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="create" options={{ presentation: 'modal' }} />
          <Stack.Screen name="setup" options={{ presentation: 'modal', gestureEnabled: false }} />
          <Stack.Screen name="login" options={{ presentation: 'modal' }} />
          <Stack.Screen name="user" />
          <Stack.Screen name="post" />
          <Stack.Screen name="post-edit" options={{ presentation: 'modal' }} />
          <Stack.Screen name="community-search" />
          <Stack.Screen name="story-create" options={{ presentation: 'modal' }} />
          <Stack.Screen name="story" />
          <Stack.Screen name="nearby" />
          <Stack.Screen name="social-inbox" />
          <Stack.Screen name="bookmarks" />
          <Stack.Screen name="profile-edit" options={{ presentation: 'modal' }} />
          <Stack.Screen name="monitor" />
          <Stack.Screen name="pet" />
          <Stack.Screen name="collect" options={{ gestureEnabled: false }} />
        </Stack>
      </AuthProvider>
    </ThemeProvider>
  );
}
