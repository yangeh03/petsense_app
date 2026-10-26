import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
import { BottomTabInset } from '@/constants/theme';
import { useObservations } from '@/hooks/use-observations';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';

/** Coordinates require both real UWB measurements and a calibrated layout; never substitute a demo route. */
export default function TrackingScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { dashboard } = useObservations('day');
  const { latest, isFresh } = useTelemetry();
  const matching = latest?.deviceId === dashboard?.pet?.device_id && isFresh ? latest : null;
  const hasRanges = matching?.uwb?.valid && matching.uwb.distanceMm.length > 0;
  return (
    <ScrollView style={{ backgroundColor: theme.background }} contentContainerStyle={styles.scroll}>
      <View
        style={[
          styles.container,
          { paddingTop: insets.top + 24, paddingBottom: BottomTabInset + insets.bottom + 110 },
        ]}
      >
        <ThemedText style={styles.title}>位置</ThemedText>
        <ThemedText themeColor="textSecondary">只显示项圈实际提供的定位能力。</ThemedText>
        <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <View style={[styles.icon, { backgroundColor: theme.tintBackground }]}>
            <SymbolView
              name={{ ios: 'location.slash', android: 'location_off', web: 'location_off' }}
              size={32}
              tintColor={theme.tint}
            />
          </View>
          <ThemedText style={styles.sectionTitle}>
            {hasRanges ? '已收到测距，尚未完成户型标定' : '当前还不能确定它的位置'}
          </ThemedText>
          <ThemedText themeColor="textSecondary" style={styles.description}>
            {hasRanges
              ? '项圈提供了真实测距数据，但尚不能据此确定房间。完成锚点与户型标定后，才能显示位置。'
              : '当前没有收到可用的位置坐标，因此无法显示房间或移动轨迹。动作识别仍可在「今日」查看。'}
          </ThemedText>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/monitor')}
            style={[styles.button, { backgroundColor: theme.tintBackground }]}
          >
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              查看项圈能力 ›
            </ThemedText>
          </Pressable>
        </View>
        <View style={[styles.info, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">动作与位置是两种数据</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            识别到走动，不代表已经知道它在哪个房间。没有定位数据时，我们不会用演示轨迹代替。
          </ThemedText>
        </View>
      </View>
    </ScrollView>
  );
}
const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: { width: '100%', maxWidth: 640, paddingHorizontal: 24, gap: 20 },
  title: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  card: { borderRadius: 24, padding: 28, gap: 20, marginTop: 12, alignItems: 'flex-start' },
  icon: { padding: 18, borderRadius: 22 },
  sectionTitle: { fontSize: 21, lineHeight: 30, fontWeight: '700' },
  description: { lineHeight: 26 },
  button: { paddingHorizontal: 18, paddingVertical: 12, borderRadius: 16 },
  info: { borderTopWidth: 1, paddingTop: 22, gap: 10 },
});
