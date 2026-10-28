import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'expo-router';
import {
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
  type ScrollViewInstance,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { timeAgo } from '@/components/social/post-card';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  addReply,
  deleteComment,
  fetchCommentContext,
  fetchComments,
  fetchReplies,
} from '@/services/api';
import type { Post, PostComment } from '@/types/social';

// 从通知定位评论时最多向后翻这么多页；再找不到就提示用户手动翻看。
const FOCUS_PAGE_LIMIT = 5;

/** 帖子评论抽屉：列表 + 发送框。 */
export function CommentSheet({
  post,
  onClose,
  onCommented,
  focusCommentId,
}: {
  post: Post | null;
  onClose: () => void;
  onCommented: (postId: number, delta?: number) => void;
  focusCommentId?: number;
}) {
  const theme = useTheme();
  const router = useRouter();
  const { user } = useAuth();
  const insets = useSafeAreaInsets();
  const [comments, setComments] = useState<PostComment[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [replies, setReplies] = useState<Record<number, PostComment[]>>({});
  const [replyCursors, setReplyCursors] = useState<Record<number, string | null>>({});
  const [loadingReplies, setLoadingReplies] = useState<number | null>(null);
  const [replyTarget, setReplyTarget] = useState<PostComment | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const [focusNote, setFocusNote] = useState('');
  const pendingMutation = useRef<string | null>(null);
  const listRef = useRef<ScrollViewInstance>(null);
  const highlightY = useRef<number | null>(null);
  const sequence = useRef(0);
  const invalidate = () => {
    sequence.current++;
  };

  const load = useCallback(() => {
    if (!post) return;
    const postId = post.id;
    const current = ++sequence.current;
    const stale = () => current !== sequence.current;
    // 纯数据拉取，不碰 state；需要定位评论时顺着游标翻到目标所在的根评论和楼层。
    const gather = async () => {
      const first = await fetchComments(postId);
      let roots = first.comments;
      let cursor = first.page?.nextCursor ?? null;
      let loadedReplies: PostComment[] | null = null;
      let replyCursor: string | null = null;
      let rootId: number | null = null;
      if (focusCommentId) {
        const { context } = await fetchCommentContext(focusCommentId).catch(() => ({
          context: null,
        }));
        if (context && context.postId === postId) {
          rootId = context.rootId ?? context.commentId;
          let pages = 0;
          while (!roots.some((item) => item.id === rootId) && cursor && pages < FOCUS_PAGE_LIMIT) {
            const more = await fetchComments(postId, cursor);
            roots = [...roots, ...more.comments.filter((c) => !roots.some((r) => r.id === c.id))];
            cursor = more.page?.nextCursor ?? null;
            pages += 1;
          }
          if (context.rootId && roots.some((item) => item.id === rootId)) {
            const replyPage = await fetchReplies(context.rootId);
            loadedReplies = replyPage.comments;
            replyCursor = replyPage.page.nextCursor;
            pages = 0;
            while (
              !loadedReplies.some((item) => item.id === focusCommentId) &&
              replyCursor &&
              pages < FOCUS_PAGE_LIMIT
            ) {
              const more = await fetchReplies(context.rootId, replyCursor);
              loadedReplies = [...loadedReplies, ...more.comments];
              replyCursor = more.page.nextCursor;
              pages += 1;
            }
          }
        }
      }
      return { roots, cursor, loadedReplies, replyCursor, rootId };
    };
    return gather()
      .then(({ roots, cursor, loadedReplies, replyCursor, rootId }) => {
        if (stale()) return;
        setComments(roots);
        setNextCursor(cursor);
        if (rootId !== null && loadedReplies) {
          setReplies({ [rootId]: loadedReplies });
          setReplyCursors({ [rootId]: replyCursor });
        }
        if (focusCommentId) {
          const found =
            roots.some((item) => item.id === focusCommentId) ||
            (loadedReplies ?? []).some((item) => item.id === focusCommentId);
          setHighlightId(found ? focusCommentId : null);
          setFocusNote(found ? '' : '这条评论可能已删除，或在更早的评论中');
        }
        setLoadError('');
      })
      .catch(() => {
        if (!stale()) setLoadError('评论加载失败');
      })
      .finally(() => {
        if (!stale()) setLoading(false);
      });
  }, [post, focusCommentId]);

  // 父组件用 key={post.id} 重挂载，这里只需异步拉取评论
  useEffect(() => {
    void load();
    return invalidate;
  }, [load]);

  const retryLoad = () => {
    setLoading(true);
    setLoadError('');
    void load();
  };

  const openAuthor = (comment: PostComment) => {
    onClose();
    router.push(`/user?id=${comment.user_id}`);
  };

  const submit = async () => {
    if (!post || !draft.trim() || sending) return;
    if (!user) {
      router.push('/login');
      return;
    }
    setSending(true);
    setError('');
    try {
      const id = pendingMutation.current ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pendingMutation.current = id;
      const data = await addReply(post.id, draft.trim(), id, replyTarget?.id);
      if (data.comment.root_id) {
        const rootId = data.comment.root_id;
        setReplies((current) => ({
          ...current,
          [rootId]: [...(current[rootId] ?? []), data.comment],
        }));
        setComments((current) =>
          current.map((item) =>
            item.id === rootId ? { ...item, reply_count: (item.reply_count ?? 0) + 1 } : item,
          ),
        );
      } else {
        setComments((current) => [...current, data.comment]);
      }
      setDraft('');
      setReplyTarget(null);
      pendingMutation.current = null;
      onCommented(post.id, 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '评论发送失败，请重试');
    } finally {
      setSending(false);
    }
  };

  const toggleReplies = async (rootId: number) => {
    if (replies[rootId]) {
      setReplies((current) => {
        const next = { ...current };
        delete next[rootId];
        return next;
      });
      setReplyCursors((current) => {
        const next = { ...current };
        delete next[rootId];
        return next;
      });
      return;
    }
    if (loadingReplies !== null) return;
    setLoadingReplies(rootId);
    try {
      const data = await fetchReplies(rootId);
      setReplies((current) => ({ ...current, [rootId]: data.comments }));
      setReplyCursors((current) => ({ ...current, [rootId]: data.page.nextCursor }));
    } catch {
      setError('回复加载失败，请重试');
    } finally {
      setLoadingReplies(null);
    }
  };

  const loadMoreReplies = async (rootId: number) => {
    const cursor = replyCursors[rootId];
    if (!cursor || loadingReplies !== null) return;
    setLoadingReplies(rootId);
    try {
      const data = await fetchReplies(rootId, cursor);
      setReplies((current) => ({
        ...current,
        [rootId]: [
          ...(current[rootId] ?? []),
          ...data.comments.filter(
            (item) => !(current[rootId] ?? []).some((previous) => previous.id === item.id),
          ),
        ],
      }));
      setReplyCursors((current) => ({ ...current, [rootId]: data.page.nextCursor }));
    } catch {
      setError('更多回复加载失败，请重试');
    } finally {
      setLoadingReplies(null);
    }
  };

  const remove = (comment: PostComment) => {
    if (!post) return;
    Alert.alert('删除评论？', '删除后无法恢复。', [
      { text: '取消', style: 'cancel' },
      {
        text: '删除',
        style: 'destructive',
        onPress: () => {
          void deleteComment(comment.id)
            .then(() => {
              if (comment.root_id) {
                const rootId = comment.root_id;
                setReplies((current) => {
                  const remaining = (current[rootId] ?? []).filter(
                    (item) => item.id !== comment.id,
                  );
                  const next = { ...current };
                  // 楼里最后一条回复删完就收起，避免留下一个空的“收起回复”。
                  if (remaining.length) next[rootId] = remaining;
                  else delete next[rootId];
                  return next;
                });
                setComments((current) =>
                  current.map((item) =>
                    item.id === rootId
                      ? { ...item, reply_count: Math.max(0, (item.reply_count ?? 1) - 1) }
                      : item,
                  ),
                );
              } else {
                void fetchReplies(comment.id)
                  .then((data) => {
                    setComments((current) =>
                      data.comments.length
                        ? current.map((item) =>
                            item.id === comment.id ? { ...comment, body: '', deleted: true } : item,
                          )
                        : current.filter((item) => item.id !== comment.id),
                    );
                  })
                  .catch(() =>
                    setComments((current) =>
                      current.map((item) =>
                        item.id === comment.id ? { ...comment, body: '', deleted: true } : item,
                      ),
                    ),
                  );
              }
              onCommented(post.id, -1);
            })
            .catch((exc) => setError(exc instanceof Error ? exc.message : '删除失败，请重试'));
        },
      },
    ]);
  };

  const renderComment = (comment: PostComment, nested = false) => {
    const highlighted = highlightId === comment.id;
    return (
      <View
        key={comment.id}
        style={[
          styles.row,
          nested && styles.nested,
          highlighted && [styles.highlighted, { backgroundColor: theme.tintBackground }],
        ]}
      >
        <Pressable
          onPress={() => openAuthor(comment)}
          style={[styles.avatar, { backgroundColor: theme.tintBackground }]}
          accessibilityLabel={`查看 ${comment.display_name} 的主页`}
        >
          <ThemedText style={{ fontSize: 16 }}>{comment.avatar_emoji || '🙂'}</ThemedText>
        </Pressable>
        <View style={styles.texts}>
          <Pressable onPress={() => openAuthor(comment)}>
            <ThemedText type="smallBold">{comment.display_name}</ThemedText>
          </Pressable>
          <ThemedText type="small" themeColor="textSecondary">
            {comment.deleted ? '原评论已删除' : comment.body}
          </ThemedText>
          <View style={styles.actions}>
            <ThemedText
              type="small"
              themeColor="textSecondary"
              accessibilityLabel={new Date(comment.created_at).toLocaleString()}
            >
              {timeAgo(comment.created_at)}
            </ThemedText>
            {!comment.deleted && (
              <Pressable onPress={() => setReplyTarget(comment)}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  回复
                </ThemedText>
              </Pressable>
            )}
            {user?.id === comment.user_id && !comment.deleted && (
              <Pressable onPress={() => remove(comment)}>
                <ThemedText type="small" style={{ color: theme.danger }}>
                  删除
                </ThemedText>
              </Pressable>
            )}
          </View>
        </View>
      </View>
    );
  };

  return (
    <Modal visible={post !== null} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Pressable
          style={styles.backdropTouch}
          onPress={onClose}
          accessible={false}
          accessibilityLabel="关闭评论"
        >
          <ThemedView
            onStartShouldSetResponder={() => true}
            style={[
              styles.sheet,
              { backgroundColor: theme.background, paddingBottom: insets.bottom + Spacing.three },
            ]}
          >
            <View style={[styles.grabber, { backgroundColor: theme.backgroundSelected }]} />
            <ThemedText type="smallBold" style={styles.title}>
              评论
            </ThemedText>
            {!!focusNote && (
              <ThemedText type="small" themeColor="textSecondary">
                {focusNote}
              </ThemedText>
            )}
            <ScrollView
              ref={listRef}
              style={styles.list}
              contentContainerStyle={{ gap: Spacing.two }}
            >
              {loading ? (
                <ThemedText type="small" themeColor="textSecondary">
                  加载中…
                </ThemedText>
              ) : loadError ? (
                <Pressable
                  onPress={retryLoad}
                  style={styles.empty}
                  accessibilityLabel="重新加载评论"
                >
                  <ThemedText type="small" style={{ color: theme.danger }}>
                    {loadError}
                  </ThemedText>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    点击重试
                  </ThemedText>
                </Pressable>
              ) : comments.length === 0 ? (
                <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                  还没有评论，来说点什么吧
                </ThemedText>
              ) : (
                comments.map((comment) => {
                  const containsHighlight =
                    highlightId !== null &&
                    (highlightId === comment.id ||
                      (replies[comment.id] ?? []).some((reply) => reply.id === highlightId));
                  return (
                    <View
                      key={comment.id}
                      onLayout={
                        containsHighlight
                          ? (event) => {
                              // 只滚一次；包裹 View 直接挂在列表内容下，y 才是相对列表的。
                              if (highlightY.current !== null) return;
                              highlightY.current = event.nativeEvent.layout.y;
                              listRef.current?.scrollTo({
                                y: Math.max(0, highlightY.current - Spacing.two),
                                animated: true,
                              });
                            }
                          : undefined
                      }
                    >
                      {renderComment(comment)}
                      {((comment.reply_count ?? 0) > 0 || !!replies[comment.id]) && (
                        <Pressable
                          onPress={() => void toggleReplies(comment.id)}
                          style={styles.replyToggle}
                        >
                          <ThemedText type="small" style={{ color: theme.tint }}>
                            {replies[comment.id]
                              ? '收起回复'
                              : `查看 ${comment.reply_count ?? 0} 条回复`}
                          </ThemedText>
                        </Pressable>
                      )}
                      {(replies[comment.id] ?? []).map((reply) => renderComment(reply, true))}
                      {!!replyCursors[comment.id] && (
                        <Pressable
                          onPress={() => void loadMoreReplies(comment.id)}
                          style={styles.replyToggle}
                        >
                          <ThemedText type="small" style={{ color: theme.tint }}>
                            {loadingReplies === comment.id ? '加载中…' : '加载更多回复'}
                          </ThemedText>
                        </Pressable>
                      )}
                    </View>
                  );
                })
              )}
              {!!nextCursor && (
                <Pressable
                  onPress={() => {
                    if (!post) return;
                    void fetchComments(post.id, nextCursor)
                      .then((data) => {
                        setComments((current) => [
                          ...current,
                          ...data.comments.filter(
                            (item) => !current.some((previous) => previous.id === item.id),
                          ),
                        ]);
                        setNextCursor(data.page.nextCursor);
                      })
                      .catch(() => setError('更多评论加载失败，请重试'));
                  }}
                >
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    加载更多评论
                  </ThemedText>
                </Pressable>
              )}
            </ScrollView>
            {!!error && (
              <ThemedText type="small" style={{ color: theme.danger }}>
                {error}
              </ThemedText>
            )}
            {!!replyTarget && (
              <Pressable onPress={() => setReplyTarget(null)}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  回复 {replyTarget.display_name} · 取消
                </ThemedText>
              </Pressable>
            )}
            <View style={[styles.inputRow, { borderColor: theme.backgroundSelected }]}>
              <TextInput
                value={draft}
                onChangeText={(value) => {
                  setDraft(value);
                  pendingMutation.current = null;
                }}
                placeholder={user ? '友善地留言…' : '登录后评论'}
                placeholderTextColor={theme.textSecondary}
                style={[styles.input, { color: theme.text }]}
                maxLength={300}
                returnKeyType="send"
                onSubmitEditing={submit}
              />
              <Pressable
                onPress={submit}
                disabled={!draft.trim() || sending}
                style={({ pressed }) => [
                  styles.send,
                  { backgroundColor: draft.trim() ? theme.tint : theme.backgroundSelected },
                  pressed && { opacity: 0.7 },
                ]}
              >
                <ThemedText
                  type="smallBold"
                  style={{ color: draft.trim() ? '#ffffff' : theme.textSecondary }}
                >
                  {sending ? '发送中' : '发送'}
                </ThemedText>
              </Pressable>
            </View>
          </ThemedView>
        </Pressable>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(20, 12, 6, 0.35)', justifyContent: 'flex-end' },
  backdropTouch: { flex: 1, justifyContent: 'flex-end' },
  sheet: {
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.two,
    maxHeight: '70%',
  },
  grabber: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    marginBottom: Spacing.two,
  },
  title: { marginBottom: Spacing.two },
  list: { flexGrow: 0 },
  empty: { textAlign: 'center', paddingVertical: Spacing.four },
  row: { flexDirection: 'row', gap: Spacing.three },
  highlighted: { padding: Spacing.two, borderRadius: 12 },
  nested: { marginLeft: Spacing.four, marginTop: Spacing.two },
  actions: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  replyToggle: { marginLeft: Spacing.five, marginVertical: Spacing.two },
  avatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  texts: { gap: 2, flexShrink: 1 },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: Spacing.three,
    paddingVertical: 6,
    marginTop: Spacing.three,
  },
  input: { flex: 1, paddingVertical: 8, fontSize: 14 },
  send: { borderRadius: 12, paddingHorizontal: Spacing.three, paddingVertical: 8 },
});
