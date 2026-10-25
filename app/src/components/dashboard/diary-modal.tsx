import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
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
  const [durationMin, setDurationMin] = useState(activeMinutes > 0 ? String(activeMinutes) : '');
  const [steps, setSteps] = useState('');
  const [places, setPlaces] = useState('');
  const [notes, setNotes] = useState('');
  const [generating, setGenerating] = useState(false);
  const [posterUrl, setPosterUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [photos, setPhotos] = useState<{ uri: string; name: string; type: string }[]>([]);

  const pickPhotos = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.8,
      allowsMultipleSelection: true,
      selectionLimit: 3,
    });
    const assets = (result.assets ?? []).slice(0, 3);
    if (assets.length) {
      setPhotos(
        assets.map((asset) => ({
          uri: asset.uri,
          name: asset.fileName ?? 'walk.jpg',
          type: asset.mimeType ?? 'image/jpeg',
        })),
      );
    }
  };

  const generate = async () => {
    if (generating) return;
    if (
      !durationMin.trim() ||
      !steps.trim() ||
      !Number.isFinite(Number(durationMin)) ||
      Number(durationMin) <= 0 ||
      !Number.isFinite(Number(steps)) ||
      Number(steps) < 0
    ) {
      Alert.alert('请补充运动记录', '时长和步数由你确认；项圈目前没有提供步数。');
      return;
    }
    setGenerating(true);
    try {
      const result = await generateDiary(petgifId ?? 'default', {
        durationMin: Number(durationMin),
        steps: Number(steps) || 0,
        places: places.trim(),
        notes: notes.trim() || `${petName} 今天的运动日记`,
        photos,
      });
      if (result.posterUrl) {
        setPosterUrl(result.posterUrl);
      } else {
        Alert.alert(
          result.posterUrl === null && result.status === 'complete' ? '生成失败' : '生成超时',
          result.message ?? 'AI 绘制约需 2 分钟，请稍后再试',
        );
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
            用你确认的运动记录，为 {petName} 制作日记海报。步数需手动填写。
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
                手动估计步数
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

          <Pressable onPress={pickPhotos} disabled={generating} style={styles.photoRow}>
            <SymbolView
              name={{
                ios: 'photo.on.rectangle.angled',
                android: 'photo_library',
                web: 'photo_library',
              }}
              size={18}
              tintColor={theme.tint}
            />
            <ThemedText type="small" style={styles.photoRowText}>
              {photos.length
                ? `已选 ${photos.length} 张遛弯照片`
                : '添加遛弯照片（可选，无照片用形象照）'}
            </ThemedText>
            {photos.length > 0 && (
              <View style={styles.photoThumbs}>
                {photos.map((photo) => (
                  <Image
                    key={photo.uri}
                    source={{ uri: photo.uri }}
                    style={styles.photoThumb}
                    contentFit="cover"
                  />
                ))}
              </View>
            )}
          </Pressable>

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
                <View style={styles.generatingRow}>
                  <ActivityIndicator color="#ffffff" size="small" />
                  <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                    AI 绘制中，约 2 分钟…
                  </ThemedText>
                </View>
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
  photoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 14,
    borderColor: 'rgba(148,131,111,0.3)',
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
  },
  photoRowText: { flex: 1, flexShrink: 1 },
  photoThumbs: { flexDirection: 'row', gap: 4 },
  photoThumb: { width: 28, height: 28, borderRadius: 6 },
  generatingRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
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
