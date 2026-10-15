import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { SymbolView } from 'expo-symbols';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * AI 角色卡生成：上传宠物照片 → PetGIF 生成专属数字形象与角色卡 → 一键设为宠物头像。
 * 生成约需 1–2 分钟，界面每 5 秒轮询一次状态。
 */
export function CharacterCardModal({
  visible,
  petId,
  petName,
  onClose,
  onBound,
}: {
  visible: boolean;
  petId: number;
  petName: string;
  onClose: () => void;
  onBound: () => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [image, setImage] = useState<{ uri: string; name: string; type: string } | null>(null);
  const [petgifId, setPetgifId] = useState<string | null>(null);
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pickImage = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.9,
      allowsMultipleSelection: false,
    });
    const asset = result.assets?.[0];
    if (asset?.uri) {
      setImage({
        uri: asset.uri,
        name: asset.fileName ?? 'pet.jpg',
        type: asset.mimeType ?? 'image/jpeg',
      });
      setCardUrl(null);
      setPetgifId(null);
    }
  };

  const generate = useCallback(async () => {
    if (!image) return;
    setBusy(true);
    try {
      const { createPetgifPetSync } = await import('@/services/petgif');
      const result = await createPetgifPetSync({ image, name: petName });
      setPetgifId(result.petgifId);
      if (result.characterCardUrl) setCardUrl(result.characterCardUrl);
    } catch {
      Alert.alert('生成任务创建失败', '请稍后重试');
    } finally {
      setBusy(false);
    }
  }, [image, petName]);

  const bind = async () => {
    if (!cardUrl || !petgifId) return;
    setBusy(true);
    try {
      const { bindPetgif, setPetAvatarImage } = await import('@/services/petgif');
      await bindPetgif(petId, petgifId);
      await setPetAvatarImage(petId, cardUrl);
      onBound();
      onClose();
    } catch {
      Alert.alert('设置失败', '请稍后重试');
    } finally {
      setBusy(false);
    }
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
            AI 角色卡 · {petName}
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
            上传一张{petName}的清晰照片，AI 会生成专属数字形象与角色卡
          </ThemedText>

          {image ? (
            <View style={[styles.preview, { backgroundColor: theme.tintBackground }]}>
              <Image source={{ uri: image.uri }} style={styles.previewImage} contentFit="cover" />
              {cardUrl && (
                <View style={styles.cardWrap}>
                  <Image source={{ uri: cardUrl }} style={styles.previewImage} contentFit="cover" />
                  <ThemedText type="smallBold" style={styles.cardBadge}>
                    角色卡已生成
                  </ThemedText>
                </View>
              )}
            </View>
          ) : null}
          {image && !petgifId && (
            <Pressable
              onPress={generate}
              disabled={busy}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              {busy ? (
                <ActivityIndicator color="#ffffff" />
              ) : (
                <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                  ✨ 开始生成
                </ThemedText>
              )}
            </Pressable>
          )}
          {false && (
            <Pressable
              onPress={pickImage}
              style={[styles.pickButton, { borderColor: theme.backgroundSelected }]}
            >
              <SymbolView
                name={{
                  ios: 'photo.on.rectangle.angled',
                  android: 'photo_library',
                  web: 'photo_library',
                }}
                size={30}
                tintColor={theme.textSecondary}
              />
              <ThemedText type="small" themeColor="textSecondary">
                选择宠物照片
              </ThemedText>
            </Pressable>
          )}

          {petgifId && !cardUrl && (
            <View style={styles.progress}>
              <ActivityIndicator color={theme.tint} />
              <ThemedText type="small" themeColor="textSecondary">
                AI 生成中（约 1–2 分钟）…
              </ThemedText>
            </View>
          )}

          {cardUrl && (
            <Pressable
              onPress={bind}
              disabled={busy}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                设为 {petName} 的形象
              </ThemedText>
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
  pickButton: {
    height: 180,
    borderRadius: 18,
    borderWidth: 1.5,
    borderStyle: 'dashed',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
  },
  preview: {
    height: 220,
    borderRadius: 18,
    overflow: 'hidden',
    flexDirection: 'row',
    gap: 2,
  },
  previewImage: { width: '50%', height: '100%' },
  cardWrap: {
    position: 'absolute',
    right: 0,
    top: 0,
    bottom: 0,
    width: '50%',
    borderLeftWidth: 2,
    borderLeftColor: '#ffffff',
  },
  cardBadge: {
    position: 'absolute',
    bottom: 6,
    alignSelf: 'center',
    color: '#ffffff',
    fontSize: 11,
    backgroundColor: 'rgba(0,0,0,0.5)',
    paddingHorizontal: 6,
    borderRadius: 8,
    overflow: 'hidden',
  },
  progress: { alignItems: 'center', gap: Spacing.two },
  primaryButton: {
    borderRadius: 16,
    alignItems: 'center',
    paddingVertical: 12,
  },
});
