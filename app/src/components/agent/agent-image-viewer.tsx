import { Image, type ImageSource } from 'expo-image';
import { Modal, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';

type Props = {
  images: ImageSource[];
  imageIndex: number;
  visible: boolean;
  onRequestClose: () => void;
  onImageIndexChange: (index: number) => void;
};

export default function AgentImageViewer({
  images,
  imageIndex,
  visible,
  onRequestClose,
  onImageIndexChange,
}: Props) {
  const theme = useTheme();
  const source = images[imageIndex];
  return (
    <Modal
      visible={visible && Boolean(source)}
      onRequestClose={onRequestClose}
      animationType="fade"
    >
      <View style={[styles.panel, { backgroundColor: theme.background }]}>
        <View style={styles.controls}>
          <Pressable
            accessibilityRole="button"
            disabled={imageIndex === 0}
            onPress={() => onImageIndexChange(imageIndex - 1)}
          >
            <ThemedText themeColor={imageIndex === 0 ? 'textSecondary' : 'text'}>上一张</ThemedText>
          </Pressable>
          <Pressable accessibilityRole="button" onPress={onRequestClose}>
            <ThemedText>关闭</ThemedText>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={imageIndex >= images.length - 1}
            onPress={() => onImageIndexChange(imageIndex + 1)}
          >
            <ThemedText themeColor={imageIndex >= images.length - 1 ? 'textSecondary' : 'text'}>
              下一张
            </ThemedText>
          </Pressable>
        </View>
        <Image
          source={source}
          contentFit="contain"
          style={styles.image}
          accessibilityLabel={`第 ${imageIndex + 1} 张，共 ${images.length} 张`}
        />
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  panel: { flex: 1, padding: 16, gap: 16 },
  controls: { flexDirection: 'row', justifyContent: 'space-between' },
  image: { flex: 1, width: '100%' },
});
