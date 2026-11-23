import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { useColorScheme } from 'react-native';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import { AuthProvider } from '@/hooks/use-auth';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();
  return (
    <KeyboardProvider>
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
            <Stack.Screen name="assistant-settings" options={{ presentation: 'modal' }} />
            <Stack.Screen
              name="records"
              options={{ headerShown: true, title: '记录', headerBackButtonDisplayMode: 'minimal' }}
            />
            <Stack.Screen
              name="exercise-diary"
              options={{
                headerShown: true,
                title: '运动日记',
                headerBackButtonDisplayMode: 'minimal',
              }}
            />
            <Stack.Screen name="monitor" />
            <Stack.Screen
              name="agent-tasks"
              options={{
                headerShown: true,
                title: '委托簿',
                headerBackButtonDisplayMode: 'minimal',
              }}
            />
            <Stack.Screen name="pet" />
            <Stack.Screen name="collect" options={{ gestureEnabled: false }} />
            <Stack.Screen
              name="uwb-setup"
              options={{
                headerShown: true,
                title: '室内地图设置',
                headerBackButtonDisplayMode: 'minimal',
              }}
            />
            <Stack.Screen
              name="dog-map"
              options={{
                headerShown: true,
                title: '遛狗地图',
                headerBackButtonDisplayMode: 'minimal',
              }}
            />
          </Stack>
        </AuthProvider>
      </ThemeProvider>
    </KeyboardProvider>
  );
}
