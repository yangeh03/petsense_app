/**
 * 伙伴会话输入区（W01 自建容器层；W07 图片附件）：指向选择、多行输入、发送、
 * 相册/拍照附件（预览条 + 逐张上传状态与重试）；键盘协调由外层的
 * react-native-keyboard-controller 负责（agent-w01-spec.md §4.2，agent-w07-spec.md §4.1）。
 * 双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4）：账号开放时发言对象多一项「两位都回」；
 * 引用了某一条回复时在输入框上方显示引用预览，可取消。
 */

import { useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { agentImageSource } from '@/services/agent';
import type { AgentAttachment, AgentTarget } from '@/stores/agent-chat';
import { assistantDisplayName, useCompanionSettingsStore } from '@/stores/companion-settings';

type Props = {
  draft: string;
  target: AgentTarget;
  petName: string | null;
  busy: boolean;
  canSend: boolean;
  keyboardOpen: boolean;
  bottomInset: number;
  attachments: AgentAttachment[];
  canAttach: boolean;
  /** 服务端已确认当前账号开放双 Agent：发言对象里才提供「两位都回」 */
  dualAgent: boolean;
  /** 正在引用的回复，没有引用时为空 */
  quote: { name: string; text: string } | null;
  onCancelQuote: () => void;
  onDraft: (draft: string) => void;
  onTarget: (target: AgentTarget) => void;
  onSend: () => void;
  onPick: (source: 'library' | 'camera') => void;
  onRetryAttachment: (key: string) => void;
  onRemoveAttachment: (key: string) => void;
};

const MIN_INPUT_HEIGHT = 40;
const MAX_INPUT_HEIGHT = 108;

function AttachmentThumb({
  item,
  onRetry,
  onRemove,
}: {
  item: AgentAttachment;
  onRetry: (key: string) => void;
  onRemove: (key: string) => void;
}) {
  const theme = useTheme();
  const source = item.imageId
    ? agentImageSource(item.imageId)
    : { uri: item.localUri ?? undefined };
  return (
    <View
      style={[
        styles.thumb,
        { backgroundColor: theme.backgroundSelected, borderColor: theme.backgroundSelected },
      ]}
    >
      <Pressable
        accessibilityRole="image"
        accessibilityLabel={item.status === 'failed' ? '图片上传失败，点击重试' : '聊天图片'}
        onPress={() => item.status === 'failed' && onRetry(item.key)}
        style={StyleSheet.absoluteFill}
      >
        <Image source={source} style={StyleSheet.absoluteFill} contentFit="cover" />
      </Pressable>
      {item.status === 'uploading' ? (
        <View style={[styles.thumbVeil, styles.thumbCenter]}>
          <ActivityIndicator size="small" color={theme.tint} />
        </View>
      ) : null}
      {item.status === 'failed' ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="重试上传图片"
          style={[styles.thumbVeil, styles.thumbCenter]}
          onPress={() => onRetry(item.key)}
        >
          <SymbolView
            name={{ ios: 'arrow.clockwise', android: 'refresh', web: 'refresh' }}
            tintColor={theme.danger}
            size={18}
          />
        </Pressable>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="移除图片"
        style={[styles.thumbRemove, { backgroundColor: theme.background }]}
        onPress={() => onRemove(item.key)}
      >
        <SymbolView
          name={{ ios: 'xmark', android: 'close', web: 'close' }}
          tintColor={theme.text}
          size={11}
        />
      </Pressable>
    </View>
  );
}

export function AgentComposer({
  draft,
  target,
  petName,
  busy,
  canSend,
  keyboardOpen,
  bottomInset,
  attachments,
  canAttach,
  dualAgent,
  quote,
  onCancelQuote,
  onDraft,
  onTarget,
  onSend,
  onPick,
  onRetryAttachment,
  onRemoveAttachment,
}: Props) {
  const theme = useTheme();
  const [showTargets, setShowTargets] = useState(false);
  const [showPickSource, setShowPickSource] = useState(false);
  const settings = useCompanionSettingsStore((state) => state.settings);
  const assistantName = assistantDisplayName(settings); // W03：助手名从设定读取
  const uploading = attachments.some((item) => item.status === 'uploading');
  const firstFailed = attachments.find((item) => item.status === 'failed');
  const targetLabel =
    target === 'pet'
      ? (petName ?? '爱宠')
      : target === 'assistant'
        ? assistantName
        : target === 'both'
          ? '两位'
          : '大家';
  const targetOptions: AgentTarget[] = dualAgent
    ? ['auto', 'pet', 'assistant', 'both']
    : ['auto', 'pet', 'assistant'];
  return (
    <View style={[styles.footer, !keyboardOpen && { marginBottom: bottomInset }]}>
      {attachments.length ? (
        <View style={styles.thumbs}>
          {attachments.map((item) => (
            <AttachmentThumb
              key={item.key}
              item={item}
              onRetry={onRetryAttachment}
              onRemove={onRemoveAttachment}
            />
          ))}
        </View>
      ) : null}
      {/* 上传状态提示（spec §4.1/§5）：上传中说明为何不可发；失败透出服务端原因（含超配额清理提示） */}
      {uploading ? (
        <ThemedText type="small" themeColor="textSecondary" style={styles.uploadHint}>
          图片上传中…
        </ThemedText>
      ) : firstFailed ? (
        <ThemedText type="small" themeColor="danger" style={styles.uploadHint}>
          {firstFailed.failReason
            ? `${firstFailed.failReason}，可点图片重试或移除`
            : '有图片未上传成功，可点图片重试或移除后再发送'}
        </ThemedText>
      ) : null}
      {/* 引用预览：这条话是接着谁的哪一句说的；取消后只是不带上引用，仍留在当前会话 */}
      {quote ? (
        <View style={[styles.quote, { backgroundColor: theme.backgroundSelected }]}>
          <ThemedText
            type="small"
            themeColor="textSecondary"
            numberOfLines={1}
            style={styles.quoteText}
          >
            {`引用 ${quote.name}：${quote.text}`}
          </ThemedText>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="取消引用"
            hitSlop={8}
            style={styles.quoteCancel}
            onPress={onCancelQuote}
          >
            <SymbolView
              name={{ ios: 'xmark', android: 'close', web: 'close' }}
              tintColor={theme.text}
              size={11}
            />
          </Pressable>
        </View>
      ) : null}
      {showTargets ? (
        <View style={styles.targets}>
          {targetOptions.map((value) => (
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
                {value === 'both'
                  ? '两位都回'
                  : value === 'pet'
                    ? (petName ?? '爱宠')
                    : value === 'assistant'
                      ? assistantName
                      : '大家'}
              </ThemedText>
            </Pressable>
          ))}
        </View>
      ) : null}
      {showPickSource ? (
        <View style={styles.targets}>
          <Pressable
            accessibilityRole="button"
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
            onPress={() => {
              setShowPickSource(false);
              onPick('library');
            }}
          >
            <ThemedText type="smallBold">相册</ThemedText>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
            onPress={() => {
              setShowPickSource(false);
              onPick('camera');
            }}
          >
            <ThemedText type="smallBold">拍摄</ThemedText>
          </Pressable>
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
          onPress={() => {
            setShowTargets(!showTargets);
            setShowPickSource(false);
          }}
        >
          <ThemedText type="smallBold">{target === 'auto' ? '@' : targetLabel}</ThemedText>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="添加图片"
          accessibilityState={{ disabled: !canAttach, expanded: showPickSource }}
          disabled={!canAttach}
          style={[styles.targetButton, { backgroundColor: theme.backgroundSelected }]}
          onPress={() => {
            setShowPickSource(!showPickSource);
            setShowTargets(false);
          }}
        >
          <SymbolView
            name={{ ios: 'photo', android: 'photo_library', web: 'photo_library' }}
            tintColor={canAttach ? theme.text : theme.textSecondary}
            size={18}
          />
        </Pressable>
        <View style={styles.inputColumn}>
          {Platform.OS !== 'web' && (
            // 原生输入框在 JS 恢复草稿时不一定发出内容尺寸事件，由同列文本决定高度。
            <Text
              accessibilityElementsHidden
              importantForAccessibility="no-hide-descendants"
              pointerEvents="none"
              style={[styles.input, styles.inputMeasure]}
            >
              {`${draft}\u200b`}
            </Text>
          )}
          <TextInput
            value={draft}
            onChangeText={onDraft}
            multiline
            maxLength={4000}
            editable={!busy}
            placeholder="说给伙伴听…"
            placeholderTextColor={theme.textSecondary}
            style={[
              styles.input,
              { color: theme.text },
              Platform.OS !== 'web' && StyleSheet.absoluteFill,
            ]}
            accessibilityLabel="给伙伴的消息"
          />
        </View>
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
  // 开放双 Agent 后有四项，名字长时自动换行，不挤出屏幕
  targets: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingLeft: 6 },
  quote: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginLeft: 6,
    borderRadius: 12,
    paddingLeft: 10,
    paddingRight: 2,
    minHeight: 34,
  },
  quoteText: { flex: 1 },
  quoteCancel: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  thumbs: { flexDirection: 'row', gap: 8, paddingLeft: 6 },
  uploadHint: { paddingLeft: 6 },
  thumb: {
    width: 56,
    height: 56,
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
  },
  thumbVeil: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.35)',
  },
  thumbCenter: { alignItems: 'center', justifyContent: 'center' },
  thumbRemove: {
    position: 'absolute',
    top: 3,
    right: 3,
    width: 18,
    height: 18,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
  },
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
  inputColumn: {
    flex: 1,
    minHeight: MIN_INPUT_HEIGHT,
    maxHeight: MAX_INPUT_HEIGHT,
    overflow: 'hidden',
  },
  input: {
    minHeight: MIN_INPUT_HEIGHT,
    maxHeight: MAX_INPUT_HEIGHT,
    paddingHorizontal: 0,
    paddingVertical: 9,
    fontSize: 16,
    lineHeight: 22,
    textAlignVertical: 'top',
  },
  inputMeasure: { opacity: 0 },
  send: { width: 40, height: 40, borderRadius: 20, justifyContent: 'center', alignItems: 'center' },
  pill: { borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8, alignSelf: 'center' },
});
