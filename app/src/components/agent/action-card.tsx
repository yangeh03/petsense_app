/**
 * W10 动作回执卡（agent-w10-spec.md §2）：远程录音 / 运动日记 / 社区草稿。
 * 状态以服务端持久结果为真（WS 补发或 HTTP 拉取按 id 覆盖）；录音完成才提供播放，
 * 播放走鉴权地址（D5：复用 WebView / Web 播放能力，仅点击播放时读取，OTA 兼容）；
 * 日记只在任务真实完成后给出详情入口；失败与未知如实展示，不提供自动重试。
 */

import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { SymbolView, type AndroidSymbol, type SFSymbol } from 'expo-symbols';
import { useRouter } from 'expo-router';

import { ActionAudio } from '@/components/agent/action-audio';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { agentRecordingUrl, type AgentAction } from '@/services/agent';

type MultiPlatformIcon = { ios: SFSymbol; android: AndroidSymbol; web: AndroidSymbol };

const KIND_META: Record<string, { title: string; icon: MultiPlatformIcon }> = {
  record_audio: {
    title: '远程录音',
    icon: { ios: 'waveform', android: 'graphic_eq', web: 'graphic_eq' },
  },
  create_diary: {
    title: '运动日记',
    icon: { ios: 'square.and.pencil', android: 'edit_note', web: 'edit_note' },
  },
  social_draft: {
    title: '社区草稿',
    icon: { ios: 'doc.text', android: 'description', web: 'description' },
  },
};

function statusTone(status: string): 'pending' | 'ok' | 'error' | 'unknown' {
  if (status === 'completed') return 'ok';
  if (status === 'failed') return 'error';
  if (status === 'unknown') return 'unknown';
  return 'pending';
}

export function ActionCard({ action, petId }: { action: AgentAction; petId: number | null }) {
  const theme = useTheme();
  const router = useRouter();
  const [playing, setPlaying] = useState(false);
  const meta = KIND_META[action.kind] ?? {
    title: '动作',
    icon: { ios: 'circle.grid.2x2', android: 'apps', web: 'apps' },
  };
  const tone = statusTone(action.status);
  const toneColor =
    tone === 'ok' ? theme.online : tone === 'error' ? theme.danger : theme.textSecondary;
  const statusLabel =
    action.status === 'executing'
      ? action.kind === 'record_audio' && action.commandSent
        ? '指令已发送，等待回传…'
        : '处理中…'
      : action.status === 'completed'
        ? '已完成'
        : action.status === 'unknown'
          ? '结果未知，等待原结果'
          : '未完成';

  const openDiary = () => {
    if (petId != null)
      router.push({ pathname: '/exercise-diary', params: { petId: String(petId) } });
  };
  const openCompose = () => router.push('/create');

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.head}>
        <SymbolView name={meta.icon} tintColor={theme.tint} size={16} />
        <ThemedText type="smallBold" style={styles.headText}>
          {meta.title}
          {action.kind === 'record_audio' && action.durationSec
            ? `（${action.durationSec} 秒）`
            : ''}
        </ThemedText>
        {action.status === 'executing' ? (
          <ActivityIndicator size="small" color={theme.tint} />
        ) : (
          <ThemedText type="small" style={{ color: toneColor }}>
            {statusLabel}
          </ThemedText>
        )}
      </View>
      <ThemedText type="small">{action.text}</ThemedText>
      {action.kind === 'record_audio' && action.deviceName ? (
        <ThemedText type="small" themeColor="textSecondary">
          目标项圈：{action.deviceName}
        </ThemedText>
      ) : null}
      {action.kind === 'record_audio' && action.status === 'completed' && action.audioUrl ? (
        playing ? (
          <ActionAudio url={agentRecordingUrl(action.id)} />
        ) : (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="播放项圈录音"
            onPress={() => setPlaying(true)}
            style={[styles.play, { borderColor: theme.backgroundSelected }]}
          >
            <SymbolView
              name={{ ios: 'play.fill', android: 'play_arrow', web: 'play_arrow' }}
              tintColor={theme.tint}
              size={14}
            />
            <ThemedText type="small" style={{ color: theme.tint }}>
              播放录音
            </ThemedText>
          </Pressable>
        )
      ) : null}
      {action.kind === 'create_diary' && action.status === 'completed' && petId != null ? (
        <Pressable accessibilityRole="link" onPress={openDiary} style={styles.link}>
          <ThemedText type="small" style={{ color: theme.tint }}>
            打开日记详情
          </ThemedText>
          <SymbolView
            name={{ ios: 'arrow.up.right', android: 'north_east', web: 'north_east' }}
            tintColor={theme.tint}
            size={12}
          />
        </Pressable>
      ) : null}
      {action.kind === 'social_draft' && action.status === 'completed' ? (
        <Pressable accessibilityRole="link" onPress={openCompose} style={styles.link}>
          <ThemedText type="small" style={{ color: theme.tint }}>
            去发布页查看草稿
          </ThemedText>
          <SymbolView
            name={{ ios: 'arrow.up.right', android: 'north_east', web: 'north_east' }}
            tintColor={theme.tint}
            size={12}
          />
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headText: { flex: 1 },
  play: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 12,
    minHeight: 36,
    alignSelf: 'flex-start',
  },
  link: { flexDirection: 'row', alignItems: 'center', gap: 4 },
});
