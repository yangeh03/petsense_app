import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import { generateDiary, shareDiaryPost } from '@/services/petgif';

/**
 * 运动日记生成（苹果健康式手账）：今日活跃/步数 + 地点备注 → AI 生成日记海报，
 * 可一键分享到社区。
 */
export function DiaryModal({
  visible,
  onClose,
  petName,
  petId,
  petgifId,
  activeMinutes,
  onShared,
}: {
  visible: boolean;
  onClose: () => void;
  petName: string;
  petId: number | null;
  petgifId: string | null;
  activeMinutes: number;
  onShared: () => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [durationMin, setDurationMin] = useState(String(activeMinutes || 30));
  const [steps, setSteps] = useState('8000');
  const [places, setPlaces] = useState('');
  const [notes, setNotes] = useState('');
  const [generating, setGenerating] = useState(false);
  const [posterUrl, setPosterUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  const generate = async () => {
    setGenerating(true);
    try {
      const result = await generateDiary(petgifId ?? 'default', {
        durationMin: Number(durationMin) || 30,
        steps: Number(steps) || 0,
        places: places.trim(),
        notes: notes.trim() || `${petName} 今天的运动日记`,
      });
      if (result.posterUrl) {
        setPosterUrl(result.posterUrl);
      } else {
        Alert.alert('生成超时', 'AI 还在绘制，稍后再来看看');
      }
    } catch {
      Alert.alert('生成失败', '请稍后重试');
    } finally {
      setGenerating(false);
    }
  };

  const share = () => {
    if (!posterUrl || sharing) return;
    setSharing(true);
    shareDiaryPost({
      posterUrl,
      caption: `${petName} 的运动日记 🏃 ${durationMin} 分钟 · ${places || '日常遛弯'}`,
      location: places,
      petId: petId ?? undefined,
    })
      .then(() => {
        onShared();
        onClose();
      })
      .catch(() => Alert.alert('分享失败', '请稍后重试'))
      .finally(() => setSharing(false));
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View
        style={[
          styles.sheet,
          { backgroundColor: theme.background, paddingTop: insets.top + Spacing.three },
        ]}
      >
        <View style={styles.header}>
          <ThemedText type="subtitle" style={styles.title}>
            运动日记
          </ThemedText>
          <Pressable onPress={onClose} hitSlop={10} accessibilityLabel="关闭">
            <SymbolView
              name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
              size={26}
              tintColor={theme.textSecondary}
            />
          </Pressable>
        </View>

        <ScrollView
          contentContainerStyle={[styles.form, { paddingBottom: insets.bottom + Spacing.five }]}
        >
          <ThemedText type="small" themeColor="textSecondary" style={styles.guide}>
            把 {petName} 今天的运动生成一张手账风日记海报
          </ThemedText>

          <View style={styles.statsRow}>
            <View style={[styles.statBox, { borderColor: theme.backgroundSelected }]}>
              <ThemedText type="small" themeColor="textSecondary">
                活跃（分钟）
              </ThemedText>
              <TextInput
                value={durationMin}
                onChangeText={setDurationMin}
                keyboardType="number-pad"
                style={[styles.input, { color: theme.text }]}
                maxLength={3}
              />
            </View>
            <View style={[styles.statBox, { borderColor: theme.backgroundSelected }]}>
              <ThemedText type="small" themeColor="textSecondary">
                步数（约）
              </ThemedText>
              <TextInput
                value={steps}
                onChangeText={setSteps}
                keyboardType="number-pad"
                style={[styles.input, { color: theme.text }]}
                maxLength={6}
              />
            </View>
          </View>

          <TextInput
            value={places}
            onChangeText={setPlaces}
            placeholder="去过的地点，如：小区绿地,河边步道"
            placeholderTextColor={theme.textSecondary}
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            maxLength={100}
          />
          <TextInput
            value={notes}
            onChangeText={setNotes}
            placeholder="备注（可选）"
            placeholderTextColor={theme.textSecondary}
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            maxLength={100}
          />

          {posterUrl ? (
            <View style={[styles.poster, { borderColor: theme.backgroundSelected }]}>
              <Image
                source={{ uri: absoluteUrl(posterUrl) }}
                style={styles.posterImage}
                contentFit="contain"
              />
            </View>
          ) : null}

          {!posterUrl && (
            <Pressable
              onPress={generate}
              disabled={generating}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              {generating ? (
                <ActivityIndicator color="#ffffff" />
              ) : (
                <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                  ✨ 生成运动日记
                </ThemedText>
              )}
            </Pressable>
          )}

          {posterUrl && (
            <Pressable
              onPress={share}
              disabled={sharing}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              {sharing ? (
                <ActivityIndicator color="#ffffff" />
              ) : (
                <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                  分享到社区
                </ThemedText>
              )}
            </Pressable>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  sheet: { flex: 1, alignItems: 'center' },
  header: {
    width: '100%',
    maxWidth: 640,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.three,
  },
  title: { fontSize: 22 },
  form: {
    width: '100%',
    maxWidth: 640,
    paddingHorizontal: Spacing.four,
    gap: Spacing.three,
  },
  guide: { textAlign: 'center' },
  statsRow: { flexDirection: 'row', gap: Spacing.three },
  statBox: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 8,
    gap: 4,
  },
  input: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
    fontSize: 14,
  },
  poster: {
    borderRadius: 18,
    borderWidth: 1,
    overflow: 'hidden',
    aspectRatio: 3 / 4,
  },
  posterImage: { width: '100%', height: '100%' },
  primaryButton: {
    borderRadius: 16,
    alignItems: 'center',
    paddingVertical: 12,
  },
});
