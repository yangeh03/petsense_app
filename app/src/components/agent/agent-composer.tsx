/**
 * 伙伴会话输入区（W01 自建容器层）：指向选择、多行输入、发送；
 * 键盘协调由外层的 react-native-keyboard-controller 负责（agent-w01-spec.md §4.2）。
 */

import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { AgentTarget } from '@/stores/agent-chat';

type Props = {
  draft: string;
  target: AgentTarget;
  petName: string | null;
  inTopic: boolean;
  busy: boolean;
  canSend: boolean;
  keyboardOpen: boolean;
  bottomInset: number;
  onDraft: (draft: string) => void;
  onTarget: (target: AgentTarget) => void;
  onSend: () => void;
};

export function AgentComposer({
  draft,
  target,
  petName,
  inTopic,
  busy,
  canSend,
  keyboardOpen,
  bottomInset,
  onDraft,
  onTarget,
  onSend,
}: Props) {
  const theme = useTheme();
  const [showTargets, setShowTargets] = useState(false);
  const targetLabel =
    target === 'pet' ? (petName ?? '爱宠') : target === 'assistant' ? '照料助手' : '大家';
  return (
    <View style={[styles.footer, !keyboardOpen && { marginBottom: bottomInset }]}>
      {showTargets ? (
        <View style={styles.targets}>
          {(['auto', 'pet', 'assistant'] as const).map((value) => (
            <Pressable
              key={value}
              accessibilityRole="button"
              accessibilityState={{ selected: target === value }}
              style={[
                styles.pill,
                {
                  backgroundColor:
                    target === value ? theme.tintBackground : theme.backgroundSelected,
                },
              ]}
              onPress={() => {
                onTarget(value);
                setShowTargets(false);
              }}
            >
              <ThemedText type="smallBold">
                {value === 'auto' ? '大家' : value === 'pet' ? petName : '照料助手'}
              </ThemedText>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View
        style={[
          styles.composer,
          { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
        ]}
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`选择发言对象，当前${targetLabel}`}
          accessibilityState={{ expanded: showTargets }}
          style={[styles.targetButton, { backgroundColor: theme.backgroundSelected }]}
          onPress={() => setShowTargets(!showTargets)}
        >
          <ThemedText type="smallBold">{target === 'auto' ? '@' : targetLabel}</ThemedText>
        </Pressable>
        <TextInput
          value={draft}
          onChangeText={onDraft}
          multiline
          maxLength={4000}
          editable={!busy}
          placeholder={inTopic ? '继续说这件事…' : '说给伙伴听…'}
          placeholderTextColor={theme.textSecondary}
          style={[styles.input, { color: theme.text }]}
          accessibilityLabel="给伙伴的消息"
        />
        <Pressable
          disabled={!canSend}
          onPress={onSend}
          accessibilityRole="button"
          accessibilityLabel="发送"
          accessibilityState={{ disabled: !canSend }}
          style={[
            styles.send,
            { backgroundColor: canSend ? theme.tint : theme.backgroundSelected },
          ]}
        >
          <SymbolView
            name={{ ios: 'arrow.up', android: 'arrow_upward', web: 'arrow_upward' }}
            tintColor={canSend ? theme.background : theme.textSecondary}
            size={20}
          />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  footer: {
    width: '100%',
    paddingHorizontal: 14,
    paddingBottom: 5,
    gap: 7,
  },
  targets: { flexDirection: 'row', gap: 8, paddingLeft: 6 },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    borderWidth: 1,
    padding: 6,
    borderRadius: 24,
    boxShadow: Shadows.chip,
  },
  targetButton: {
    minWidth: 40,
    minHeight: 40,
    borderRadius: 20,
    paddingHorizontal: 9,
    justifyContent: 'center',
    alignItems: 'center',
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 108,
    paddingVertical: 9,
    fontSize: 16,
    lineHeight: 22,
  },
  send: { width: 40, height: 40, borderRadius: 20, justifyContent: 'center', alignItems: 'center' },
  pill: { borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8, alignSelf: 'center' },
});
