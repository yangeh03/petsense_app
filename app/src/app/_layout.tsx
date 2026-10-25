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
          <Stack.Screen name="login" options={{ presentation: 'modal' }} />
          <Stack.Screen name="user" />
          <Stack.Screen name="monitor" />
          <Stack.Screen name="collect" options={{ gestureEnabled: false }} />
        </Stack>
      </AuthProvider>
    </ThemeProvider>
  );
}
