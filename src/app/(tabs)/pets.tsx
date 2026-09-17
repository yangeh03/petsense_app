import { SymbolView } from 'expo-symbols';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PetAvatar } from '@/components/pet-avatar';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useCurrentPet } from '@/context/current-pet';
import { pets, speciesLabel } from '@/data/pets';
import { useTheme } from '@/hooks/use-theme';

/** 宠物列表页：点击卡片切换“当前宠物”，首页概览随之联动。 */
export default function PetsScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const { currentPet, setCurrentPet } = useCurrentPet();

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        <ThemedText type="subtitle">我的宠物</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          共 {pets.length} 只 · 点击卡片切换当前宠物
        </ThemedText>

        <View style={styles.list}>
          {pets.map((pet) => {
            const selected = pet.id === currentPet.id;
            return (
              <Pressable
                key={pet.id}
                onPress={() => setCurrentPet(pet)}
                style={({ pressed }) => [pressed && styles.pressed]}
              >
                <ThemedView
                  type="backgroundElement"
                  style={[styles.card, selected && { borderColor: theme.tint, borderWidth: 1.5 }]}
                >
                  <PetAvatar emoji={pet.emoji} size={64} />
                  <View style={styles.petInfo}>
                    <View style={styles.nameRow}>
                      <ThemedText type="smallBold" style={styles.name}>
                        {pet.name}
                      </ThemedText>
                      <View style={[styles.tag, { backgroundColor: theme.tintBackground }]}>
                        <ThemedText type="small" style={{ color: theme.tint }}>
                          {speciesLabel[pet.species]} · {pet.breed}
                        </ThemedText>
                      </View>
                    </View>
                    <ThemedText type="small" themeColor="textSecondary" style={styles.bio}>
                      {pet.bio}
                    </ThemedText>
                    <View style={styles.metaRow}>
                      <ThemedText type="small" themeColor="textSecondary">
                        {pet.age} 岁 · {pet.weight} kg
                      </ThemedText>
                      <ThemedText
                        type="small"
                        style={{
                          color: pet.vaccinated ? theme.tint : theme.warning,
                        }}
                      >
                        {pet.vaccinated ? '疫苗齐全' : '疫苗待补种'}
                      </ThemedText>
                    </View>
                  </View>
                  {selected && (
                    <SymbolView
                      name={{
                        ios: 'checkmark.circle.fill',
                        android: 'check_circle',
                        web: 'check_circle',
                      }}
                      size={22}
                      tintColor={theme.tint}
                    />
                  )}
                </ThemedView>
              </Pressable>
            );
          })}
        </View>

        <Pressable style={({ pressed }) => [styles.addButton, pressed && styles.pressed]}>
          <SymbolView
            name={{ ios: 'plus.circle.fill', android: 'add_circle', web: 'add_circle' }}
            size={20}
            tintColor={theme.tint}
          />
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            添加宠物
          </ThemedText>
        </Pressable>
      </ThemedView>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollView: {
    flex: 1,
  },
  contentContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
  },
  container: {
    flexGrow: 1,
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset + Spacing.five,
    gap: Spacing.three,
  },
  list: {
    gap: Spacing.three,
    marginTop: Spacing.two,
  },
  pressed: {
    opacity: 0.7,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: Spacing.three,
    padding: Spacing.three,
    borderColor: 'transparent',
    borderWidth: 1.5,
  },
  petInfo: {
    flex: 1,
    gap: Spacing.one,
  },
  nameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    flexWrap: 'wrap',
  },
  name: {
    fontSize: 18,
  },
  tag: {
    borderRadius: Spacing.two,
    paddingHorizontal: Spacing.two,
    paddingVertical: 2,
  },
  bio: {
    flexShrink: 1,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  addButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    paddingVertical: Spacing.three,
    borderRadius: Spacing.three,
    borderStyle: 'dashed',
    borderWidth: 1,
    borderColor: 'rgba(128, 128, 128, 0.4)',
    marginTop: Spacing.two,
  },
});
