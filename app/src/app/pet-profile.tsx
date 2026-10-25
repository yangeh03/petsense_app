import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { fetchPets, updatePet } from '@/services/api';
import type { SocialPet } from '@/types/social';

export default function PetProfileScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [pet, setPet] = useState<SocialPet | null>(null);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [breed, setBreed] = useState('');
  const [species, setSpecies] = useState('dog');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const sequence = useRef(0);
  const load = useCallback(() => {
    const requestId = ++sequence.current;
    fetchPets()
      .then((data) => {
        if (requestId !== sequence.current) return;
        const item = data.pets[0];
        if (!item) {
          router.replace('/setup');
          return;
        }
        setPet(item);
        setName(item.name);
        setBreed(item.breed);
        setSpecies(item.species);
        setError('');
      })
      .catch(() => {
        if (requestId === sequence.current) setError('暂时无法读取档案，请重试。');
      })
      .finally(() => {
        if (requestId === sequence.current) setLoading(false);
      });
  }, [router]);
  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        sequence.current += 1;
      };
    }, [load]),
  );
  const save = async () => {
    if (!pet || saving || !name.trim()) return;
    setSaving(true);
    setError('');
    try {
      await updatePet(pet.id, { name: name.trim(), breed: breed.trim(), species });
      router.back();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败，请重试。');
    } finally {
      setSaving(false);
    }
  };
  const inputStyle = [
    styles.input,
    { color: theme.text, backgroundColor: theme.backgroundElement },
  ];
  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={styles.scroll}
    >
      <View style={[styles.container, { paddingTop: insets.top + 20 }]}>
        <Pressable accessibilityRole="button" onPress={() => router.back()} style={styles.back}>
          <ThemedText style={{ color: theme.tint }}>‹ 返回</ThemedText>
        </Pressable>
        <ThemedText style={styles.title}>宠物档案</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          名字和资料会用于首页、项圈与社区展示。
        </ThemedText>
        {loading ? (
          <ActivityIndicator color={theme.tint} />
        ) : (
          pet && (
            <>
              <ThemedText type="smallBold">名字</ThemedText>
              <TextInput
                accessibilityLabel="宠物名字"
                value={name}
                onChangeText={setName}
                maxLength={20}
                style={inputStyle}
              />
              <ThemedText type="smallBold">物种</ThemedText>
              <View style={styles.row}>
                {(['dog', 'cat', 'rabbit'] as const).map((value, index) => (
                  <Pressable
                    key={value}
                    accessibilityRole="button"
                    accessibilityState={{ selected: species === value }}
                    onPress={() => setSpecies(value)}
                    style={[
                      styles.option,
                      {
                        backgroundColor:
                          species === value ? theme.tintBackground : theme.backgroundElement,
                      },
                    ]}
                  >
                    <ThemedText style={{ color: species === value ? theme.tint : theme.text }}>
                      {['狗', '猫', '兔子'][index]}
                    </ThemedText>
                  </Pressable>
                ))}
              </View>
              <ThemedText type="smallBold">品种（可选）</ThemedText>
              <TextInput
                accessibilityLabel="宠物品种"
                value={breed}
                onChangeText={setBreed}
                maxLength={40}
                style={inputStyle}
              />
              {error ? (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {error}
                </ThemedText>
              ) : null}
              <Pressable
                accessibilityRole="button"
                disabled={saving || !name.trim()}
                onPress={() => void save()}
                style={[
                  styles.option,
                  {
                    backgroundColor: theme.tintBackground,
                    opacity: saving || !name.trim() ? 0.5 : 1,
                  },
                ]}
              >
                <ThemedText type="smallBold" style={{ color: theme.tint }}>
                  {saving ? '正在保存…' : '保存资料'}
                </ThemedText>
              </Pressable>
              <View style={styles.divider} />
              <ThemedText type="smallBold">项圈连接</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {pet.device_id || '还未绑定项圈'}
              </ThemedText>
              <Pressable
                accessibilityRole="button"
                onPress={() => router.push(pet.device_id ? '/monitor' : '/setup')}
                style={styles.back}
              >
                <ThemedText style={{ color: theme.tint }}>
                  {pet.device_id ? '查看项圈诊断 ›' : '连接项圈 ›'}
                </ThemedText>
              </Pressable>
              <ThemedText type="small" themeColor="textSecondary">
                每个账号支持一只宠物与一个项圈。已有生成图片中的文字不会随改名自动重绘。
              </ThemedText>
            </>
          )
        )}
        {!loading && !pet && error ? (
          <Pressable accessibilityRole="button" onPress={() => void load()}>
            <ThemedText>{error} 点击重试</ThemedText>
          </Pressable>
        ) : null}
      </View>
    </ScrollView>
  );
}
const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: { width: '100%', maxWidth: 640, padding: 24, paddingBottom: 80, gap: 16 },
  title: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  back: { paddingVertical: 8, alignSelf: 'flex-start' },
  input: { padding: 16, borderRadius: 14, fontSize: 16 },
  row: { flexDirection: 'row', gap: 12 },
  option: { padding: 16, borderRadius: 14, alignItems: 'center', minWidth: 72 },
  divider: { height: 16 },
});
