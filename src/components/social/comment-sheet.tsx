import { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { addComment, fetchComments } from '@/services/api';
import type { Post, PostComment } from '@/types/social';

/** 帖子评论抽屉：列表 + 发送框。 */
export function CommentSheet({
  post,
  onClose,
  onCommented,
}: {
  post: Post | null;
  onClose: () => void;
  onCommented: (postId: number) => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [comments, setComments] = useState<PostComment[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  // 父组件用 key={post.id} 重挂载，这里只需异步拉取评论
  useEffect(() => {
    if (!post) return;
    let cancelled = false;
    fetchComments(post.id)
      .then((data) => {
        if (!cancelled) setComments(data.comments);
      })
      .catch(() => {
        // 网络异常保持空列表
      });
    return () => {
      cancelled = true;
    };
  }, [post]);

  const submit = async () => {
    if (!post || !draft.trim() || sending) return;
    setSending(true);
    try {
      await addComment(post.id, draft.trim());
      const data = await fetchComments(post.id);
      setComments(data.comments);
      setDraft('');
      onCommented(post.id);
    } catch {
      // 失败保留草稿
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal visible={post !== null} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Pressable style={styles.backdropTouch} onPress={onClose}>
          <ThemedView
            style={[
              styles.sheet,
              { backgroundColor: theme.background, paddingBottom: insets.bottom + Spacing.three },
            ]}
          >
            <View style={[styles.grabber, { backgroundColor: theme.backgroundSelected }]} />
            <ThemedText type="smallBold" style={styles.title}>
              评论（{comments.length}）
            </ThemedText>
            <ScrollView style={styles.list} contentContainerStyle={{ gap: Spacing.two }}>
              {comments.length === 0 ? (
                <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                  还没有评论，来说点什么吧
                </ThemedText>
              ) : (
                comments.map((comment) => (
                  <View key={comment.id} style={styles.row}>
                    <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                      <ThemedText style={{ fontSize: 16 }}>
                        {comment.avatar_emoji || '🙂'}
                      </ThemedText>
                    </View>
                    <View style={styles.texts}>
                      <ThemedText type="smallBold">{comment.display_name}</ThemedText>
                      <ThemedText type="small" themeColor="textSecondary">
                        {comment.body}
                      </ThemedText>
                    </View>
                  </View>
                ))
              )}
            </ScrollView>
            <View style={[styles.inputRow, { borderColor: theme.backgroundSelected }]}>
              <TextInput
                value={draft}
                onChangeText={setDraft}
                placeholder="友善地留言…"
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
                  发送
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
