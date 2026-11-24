/**
 * 会话抽屉（W16，docs/agent-w16-spec.md §6.2）：伙伴页的会话列表，不单独占一个页面。
 *
 * - 用 RN `Modal`（透明）承载，才能盖住原生标签栏；面板从左侧滑入。关闭图标、遮罩与底部动作面板
 *   沿用通知中心的容器语言。
 * - 「⋯」的动作面板、重命名与删除确认都画在这同一个 Modal 里，不叠加第二个系统弹窗。
 * - 系统开启「减弱动态效果」时只淡入。
 */

import { useState, type ComponentProps } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import Animated, {
  FadeIn,
  SlideInDown,
  SlideInLeft,
  useReducedMotion,
} from 'react-native-reanimated';
import { useRouter } from 'expo-router';
import { SymbolView } from 'expo-symbols';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
import { timeAgo } from '@/components/social/post-card';
import { Shadows, Spacing } from '@/constants/theme';
import type { RemoveConversationResult } from '@/hooks/use-agent-conversations';
import { useTheme } from '@/hooks/use-theme';
import type { AgentConversation } from '@/services/agent';

type IconName = ComponentProps<typeof SymbolView>['name'];

type Props = {
  visible: boolean;
  list: AgentConversation[];
  activeId: number | null;
  /** 正在取列表 */
  loading: boolean;
  /** 已经取到过一次列表（空列表只在取到之后才当作真的没有会话） */
  loaded: boolean;
  /** 列表读取失败或新建失败时的提示 */
  error: string;
  /** 正在新建、改名或删除 */
  busy: boolean;
  onClose: () => void;
  onSelect: (conversation: AgentConversation) => void;
  /** 新建并切过去；成功后由调用方关闭抽屉，失败时把原因放进 error */
  onCreate: () => Promise<void>;
  /** 失败时返回可直接展示的原因，成功返回空 */
  onRename: (conversationId: number, title: string) => Promise<string | null>;
  onRemove: (conversationId: number) => Promise<RemoveConversationResult>;
  onRetry: () => void;
};

/** 抽屉里当前显示的一层：列表，或盖在它上面的动作面板 / 重命名 / 删除确认 */
type Layer =
  | { name: 'list' }
  | { name: 'actions'; conversation: AgentConversation }
  | { name: 'rename'; conversation: AgentConversation; draft: string; error: string }
  | { name: 'delete'; conversation: AgentConversation; error: string; busyHint: boolean };

const LIST: Layer = { name: 'list' };
/** 标题长度上限与服务端的改名校验一致 */
const MAX_TITLE = 40;

const titleOf = (conversation: AgentConversation) => conversation.title || '新对话';

/** 系统会话按类型区分图标与颜色；主人会话用会话图标。这里的颜色只作图标用，不作文字。 */
function kindStyle(
  conversation: AgentConversation,
  theme: ReturnType<typeof useTheme>,
): { icon: IconName; color: string } {
  if (conversation.kind === 'care') {
    if (conversation.careKind === 'device') {
      return {
        icon: { ios: 'exclamationmark.triangle.fill', android: 'warning', web: 'warning' },
        color: theme.warning,
      };
    }
    if (conversation.careKind === 'learning') {
      return {
        icon: { ios: 'graduationcap.fill', android: 'school', web: 'school' },
        color: theme.online,
      };
    }
    return {
      icon: { ios: 'calendar', android: 'calendar_month', web: 'calendar_month' },
      color: theme.info,
    };
  }
  return {
    icon: { ios: 'bubble.left.and.bubble.right', android: 'chat', web: 'chat' },
    color: theme.tint,
  };
}

function ConversationRow({
  conversation,
  active,
  onOpen,
  onMore,
}: {
  conversation: AgentConversation;
  active: boolean;
  onOpen: () => void;
  onMore: () => void;
}) {
  const theme = useTheme();
  const kind = kindStyle(conversation, theme);
  const title = titleOf(conversation);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}${conversation.unread ? `，${conversation.unread} 条未读` : ''}`}
      onPress={onOpen}
      style={({ pressed }) => [
        styles.row,
        { backgroundColor: active ? theme.tintBackground : theme.backgroundElement },
        pressed && styles.rowPressed,
      ]}
    >
      <View style={[styles.iconDot, { backgroundColor: `${kind.color}1F` }]}>
        <SymbolView name={kind.icon} tintColor={kind.color} size={18} />
      </View>
      <View style={styles.texts}>
        <ThemedText type="smallBold" numberOfLines={1}>
          {title}
        </ThemedText>
        <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
          {conversation.preview
            ? `${timeAgo(conversation.preview.at)} · ${conversation.preview.text}`
            : '还没有消息'}
        </ThemedText>
      </View>
      {conversation.unread > 0 ? (
        <View
          style={[styles.dot, { backgroundColor: theme.tint }]}
          accessibilityLabel={`${conversation.unread} 条未读`}
        />
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${title} 的更多操作`}
        hitSlop={10}
        onPress={onMore}
        style={styles.more}
      >
        <SymbolView
          name={{ ios: 'ellipsis', android: 'more_vert', web: 'more_vert' }}
          tintColor={theme.textSecondary}
          size={18}
        />
      </Pressable>
    </Pressable>
  );
}

export function ConversationDrawer({
  visible,
  list,
  activeId,
  loading,
  loaded,
  error,
  busy,
  onClose,
  onSelect,
  onCreate,
  onRename,
  onRemove,
  onRetry,
}: Props) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const reduceMotion = useReducedMotion();
  const [layer, setLayer] = useState<Layer>(LIST);

  // 关闭后回到列表层，下次打开从头开始：在渲染期间对照上一次的 visible 调整，不靠 effect
  const [wasVisible, setWasVisible] = useState(visible);
  if (visible !== wasVisible) {
    setWasVisible(visible);
    if (!visible) setLayer(LIST);
  }

  const close = () => {
    setLayer(LIST);
    onClose();
  };
  const backToList = () => setLayer(LIST);
  // 手机上取屏幕宽度的 82%、不超过 360；平板上固定 360
  const panelWidth = Math.min(width * 0.82, 360);
  const scrim = [styles.backdrop, { backgroundColor: theme.overlay }];

  const remove = async () => {
    if (layer.name !== 'delete') return;
    const conversation = layer.conversation;
    const result = await onRemove(conversation.id);
    if (result.ok) {
      onClose();
      return;
    }
    setLayer({ name: 'delete', conversation, error: result.message, busyHint: result.busy });
  };

  const rename = async () => {
    if (layer.name !== 'rename' || !layer.draft.trim()) return;
    const failure = await onRename(layer.conversation.id, layer.draft);
    if (failure) setLayer({ ...layer, error: failure });
    else backToList();
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      statusBarTranslucent
      onRequestClose={close}
    >
      <View style={styles.root} accessibilityViewIsModal accessible={false}>
        <Pressable style={scrim} onPress={close} accessible={false} />
        <Animated.View
          entering={reduceMotion ? FadeIn.duration(200) : SlideInLeft.duration(260)}
          style={[
            styles.panel,
            {
              width: panelWidth,
              backgroundColor: theme.background,
              paddingTop: insets.top,
              paddingBottom: insets.bottom,
              borderRightColor: theme.backgroundSelected,
            },
          ]}
        >
          <View style={styles.header}>
            <ThemedText type="subtitle" style={styles.title}>
              对话
            </ThemedText>
            <View style={styles.headerActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="新对话"
                accessibilityState={{ disabled: busy }}
                disabled={busy}
                onPress={() => void onCreate()}
                hitSlop={10}
                style={styles.headerButton}
              >
                <SymbolView
                  name={{ ios: 'square.and.pencil', android: 'edit', web: 'edit' }}
                  tintColor={theme.tintForeground}
                  size={22}
                />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="关闭会话列表"
                onPress={close}
                hitSlop={10}
                style={styles.headerButton}
              >
                <SymbolView
                  name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
                  tintColor={theme.textSecondary}
                  size={26}
                />
              </Pressable>
            </View>
          </View>
          {error ? (
            <Pressable accessibilityRole="button" onPress={onRetry} style={styles.state}>
              <ThemedText type="small" themeColor="danger">
                {error}
              </ThemedText>
            </Pressable>
          ) : loading && !list.length ? (
            <View style={styles.state}>
              <ActivityIndicator size="small" color={theme.tint} />
              <ThemedText type="small" themeColor="textSecondary">
                正在读取…
              </ThemedText>
            </View>
          ) : loaded && !list.length ? (
            <View style={styles.state}>
              <ThemedText type="smallBold">还没有对话</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                点右上角的新对话，开始和它聊聊。
              </ThemedText>
            </View>
          ) : (
            <ScrollView contentContainerStyle={styles.list}>
              {list.map((conversation) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === activeId}
                  onOpen={() => {
                    close();
                    onSelect(conversation);
                  }}
                  onMore={() => setLayer({ name: 'actions', conversation })}
                />
              ))}
            </ScrollView>
          )}
        </Animated.View>

        {layer.name === 'actions' ? (
          <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
            <Pressable style={scrim} onPress={backToList} accessible={false} />
            <Animated.View
              entering={reduceMotion ? FadeIn.duration(160) : SlideInDown.duration(220)}
              style={[
                styles.sheet,
                { backgroundColor: theme.background, paddingBottom: insets.bottom + Spacing.three },
              ]}
            >
              <ThemedText type="smallBold" numberOfLines={1} style={styles.sheetTitle}>
                {titleOf(layer.conversation)}
              </ThemedText>
              <Pressable
                accessibilityRole="button"
                onPress={() =>
                  setLayer({
                    name: 'rename',
                    conversation: layer.conversation,
                    draft: layer.conversation.title,
                    error: '',
                  })
                }
                style={styles.sheetAction}
              >
                <ThemedText>重命名</ThemedText>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`删除对话「${titleOf(layer.conversation)}」`}
                onPress={() =>
                  setLayer({
                    name: 'delete',
                    conversation: layer.conversation,
                    error: '',
                    busyHint: false,
                  })
                }
                style={styles.sheetAction}
              >
                <ThemedText themeColor="danger">删除对话</ThemedText>
              </Pressable>
            </Animated.View>
          </View>
        ) : null}

        {layer.name === 'rename' ? (
          <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
            <Pressable style={scrim} onPress={backToList} accessible={false} />
            <View style={[styles.dialog, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">重命名会话</ThemedText>
              <TextInput
                value={layer.draft}
                onChangeText={(draft) => setLayer({ ...layer, draft, error: '' })}
                maxLength={MAX_TITLE}
                placeholder="给这个会话起个名字"
                placeholderTextColor={theme.textSecondary}
                returnKeyType="done"
                onSubmitEditing={() => void rename()}
                editable={!busy}
                autoFocus
                style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
              />
              {layer.error ? (
                <ThemedText type="small" themeColor="danger">
                  {layer.error}
                </ThemedText>
              ) : null}
              <View style={styles.dialogActions}>
                <Pressable
                  accessibilityRole="button"
                  onPress={backToList}
                  style={styles.dialogButton}
                >
                  <ThemedText themeColor="textSecondary">取消</ThemedText>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ disabled: busy || !layer.draft.trim() }}
                  disabled={busy || !layer.draft.trim()}
                  onPress={() => void rename()}
                  style={styles.dialogButton}
                >
                  {busy ? (
                    <ActivityIndicator size="small" color={theme.tint} />
                  ) : (
                    <ThemedText themeColor="tintForeground">保存</ThemedText>
                  )}
                </Pressable>
              </View>
            </View>
          </View>
        ) : null}

        {layer.name === 'delete' ? (
          <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
            <Pressable style={scrim} onPress={backToList} accessible={false} />
            <View style={[styles.dialog, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">删除这个会话？</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                「{titleOf(layer.conversation)}」里的聊天会从列表里移除，无法恢复。
              </ThemedText>
              {layer.error ? (
                <ThemedText type="small" themeColor="danger">
                  {layer.error}
                </ThemedText>
              ) : null}
              {layer.busyHint ? (
                <Pressable
                  accessibilityRole="link"
                  onPress={() => {
                    onClose();
                    router.push('/agent-tasks');
                  }}
                  style={styles.link}
                >
                  <ThemedText type="small" themeColor="tintForeground">
                    去委托簿暂停或结束它
                  </ThemedText>
                </Pressable>
              ) : null}
              <View style={styles.dialogActions}>
                <Pressable
                  accessibilityRole="button"
                  onPress={backToList}
                  style={styles.dialogButton}
                >
                  <ThemedText themeColor="textSecondary">取消</ThemedText>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="确认删除会话"
                  accessibilityState={{ disabled: busy }}
                  disabled={busy}
                  onPress={() => void remove()}
                  style={styles.dialogButton}
                >
                  {busy ? (
                    <ActivityIndicator size="small" color={theme.danger} />
                  ) : (
                    <ThemedText themeColor="danger">删除</ThemedText>
                  )}
                </Pressable>
              </View>
            </View>
          </View>
        ) : null}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
  panel: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    borderRightWidth: StyleSheet.hairlineWidth,
    boxShadow: Shadows.card,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.three,
    paddingBottom: Spacing.two,
  },
  title: { fontSize: 22 },
  headerActions: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  headerButton: {
    alignItems: 'center',
    justifyContent: 'center',
    width: 36,
    height: 36,
  },
  list: { gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.three },
  state: {
    gap: Spacing.two,
    alignItems: 'center',
    paddingVertical: Spacing.six,
    paddingHorizontal: Spacing.four,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 16, padding: 12 },
  rowPressed: { opacity: 0.8 },
  iconDot: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  texts: { flex: 1, gap: 2 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  more: { alignItems: 'center', justifyContent: 'center', width: 28, height: 28 },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.three,
  },
  sheetTitle: { marginBottom: Spacing.two },
  sheetAction: { paddingVertical: 12 },
  dialog: {
    position: 'absolute',
    left: Spacing.four,
    right: Spacing.four,
    top: '50%',
    marginTop: -96,
    gap: 10,
    borderRadius: 20,
    padding: Spacing.four,
  },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 10,
    fontSize: 16,
  },
  dialogActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: Spacing.three },
  dialogButton: { minWidth: 64, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  link: { minHeight: 44, justifyContent: 'center', alignSelf: 'flex-start' },
});
