import { Image } from 'expo-image';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl, fetchAvatarTemplates } from '@/services/api';
import type { PetAvatarTemplate } from '@/types/social';

export function AvatarTemplatePicker({
  selectedId,
  disabled,
  onChange,
}: {
  selectedId: string | null;
  disabled: boolean;
  onChange: (id: string | null) => void;
}) {
  const theme = useTheme();
  const [templates, setTemplates] = useState<PetAvatarTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void fetchAvatarTemplates()
      .then((result) => {
        if (!active) return;
        setTemplates(result.templates);
        setError('');
      })
      .catch(() => {
        if (active) setError('动画目录暂时无法加载，已有选择会保留。');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  return (
    <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="smallBold">首页动画形象</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        预设只改变首页动作动画，不会替换宠物照片或个人数字形象。
      </ThemedText>
      {loading ? (
        <View style={styles.loading}>
          <ActivityIndicator color={theme.tint} />
          <ThemedText type="small" themeColor="textSecondary">
            正在加载动画目录…
          </ThemedText>
        </View>
      ) : error ? (
        <View style={styles.loading}>
          <ThemedText type="small" themeColor="textSecondary">
            {error}
          </ThemedText>
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              setLoading(true);
              setError('');
              setAttempt((value) => value + 1);
            }}
          >
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              重试
            </ThemedText>
          </Pressable>
        </View>
      ) : (
        <View style={styles.options}>
          {templates.map((template) => (
            <TemplateOption
              key={template.id}
              template={template}
              selected={selectedId === template.id}
              disabled={disabled}
              onPress={() => onChange(selectedId === template.id ? null : template.id)}
            />
          ))}
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected: selectedId === null }}
            disabled={disabled}
            onPress={() => onChange(null)}
            style={[
              styles.clear,
              {
                borderColor: selectedId === null ? theme.tint : theme.backgroundSelected,
                backgroundColor: selectedId === null ? theme.tintBackground : theme.background,
              },
            ]}
          >
            <ThemedText
              type="smallBold"
              style={{ color: selectedId === null ? theme.tint : theme.text }}
            >
              不使用预设
            </ThemedText>
          </Pressable>
        </View>
      )}
    </View>
  );
}

function TemplateOption({
  template,
  selected,
  disabled,
  onPress,
}: {
  template: PetAvatarTemplate;
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  const preview = template.previewUrl || template.characterCardUrl;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`选择${template.name}动画`}
      accessibilityState={{ selected }}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.option,
        {
          borderColor: selected ? theme.tint : theme.backgroundSelected,
          backgroundColor: selected ? theme.tintBackground : theme.background,
        },
      ]}
    >
      {preview ? (
        <Image
          source={{ uri: absoluteUrl(preview) }}
          contentFit="cover"
          cachePolicy="memory-disk"
          transition={120}
          style={styles.preview}
        />
      ) : (
        <View style={[styles.preview, { backgroundColor: theme.backgroundSelected }]} />
      )}
      <ThemedText type="smallBold" style={styles.name}>
        {template.name}
      </ThemedText>
      {selected && (
        <ThemedText type="small" style={{ color: theme.tint }}>
          已选择
        </ThemedText>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 20, padding: Spacing.four, gap: Spacing.two },
  loading: { minHeight: 72, justifyContent: 'center', alignItems: 'center', gap: Spacing.two },
  options: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  option: { width: 112, borderWidth: 1, borderRadius: 16, padding: 8, gap: 6 },
  preview: { width: 94, height: 94, borderRadius: 12 },
  name: { textAlign: 'center' },
  clear: {
    minHeight: 44,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: 12,
    justifyContent: 'center',
  },
});
