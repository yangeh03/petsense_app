import { useEffect, useRef, useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { request } from '@/services/api';

type Session = {
  id: string;
  tagId: number;
  phase: 'touch_ready' | 'touching' | 'anchors_ready';
  message: string;
  rows: (number[] | null)[];
  anchors: number[][] | null;
  capture: { anchor: number; endsAt: number; samples: number } | null;
};

/** Capture runs on the server, so leaving this page does not invent a result. */
export function UwbSetup({ deviceId }: { deviceId: string }) {
  const theme = useTheme();
  const [session, setSession] = useState<Session | null>(null);
  const [tag, setTag] = useState('0');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const pending = useRef(false);
  const generation = useRef(0);
  const mutation = useRef(0);

  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        if (!pending.current) {
          const revision = mutation.current;
          const result = await request<{ session: Session | null }>(
            `/uwb/setup?deviceId=${encodeURIComponent(deviceId)}`,
            { signal: controller.signal },
          );
          if (
            !stopped &&
            current === generation.current &&
            !pending.current &&
            revision === mutation.current
          ) {
            setSession(result.session);
            setError('');
          }
        }
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : '无法读取标定进度');
      } finally {
        if (!stopped) {
          setNow(Date.now());
          timer = setTimeout(poll, 1000);
        }
      }
    }
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      generation.current = current + 1;
    };
  }, [deviceId]);

  async function act(action: string, extra: Record<string, unknown> = {}) {
    if (pending.current) return;
    pending.current = true;
    mutation.current++;
    setBusy(true);
    const current = generation.current;
    try {
      const result = await request<{ session: Session }>(`/uwb/setup${action}`, {
        method: 'POST',
        body: { deviceId, ...extra },
      });
      if (current === generation.current) {
        setSession(result.session);
        setError('');
      }
    } catch (reason) {
      if (current === generation.current)
        setError(reason instanceof Error ? reason.message : '标定操作失败');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return (
    <View style={{ gap: Spacing.three }}>
      <ThemedText type="smallBold">新环境 · 基站标定</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        固定 A0、A1、A2，避免三点共线。将 Tag 依次贴近每个基站（25 cm 内），点击对应按键后保持静止 6
        秒。其余两个基站须在线。
      </ThemedText>
      {!session && (
        <>
          <TextInput
            accessibilityLabel="Tag ID"
            value={tag}
            onChangeText={setTag}
            keyboardType="number-pad"
            placeholder="Tag ID（当前实验为 0）"
            placeholderTextColor={theme.textSecondary}
            style={{
              color: theme.text,
              padding: Spacing.three,
              borderWidth: 1,
              borderColor: theme.backgroundSelected,
            }}
          />
          <ActionButton
            label="开始 / 恢复标定"
            onPress={() => void act('', { tagId: Number(tag) })}
            disabled={busy || !/^\d+$/.test(tag) || Number(tag) > 65535}
          />
        </>
      )}
      {session && (
        <>
          <ThemedText type="small">
            Tag {session.tagId} · {session.message}
          </ThemedText>
          {[0, 1, 2].map((index) => (
            <View key={index}>
              <ActionButton
                label={`A${index} · ${session.rows[index] ? '已采集，点击重采' : '贴近后开始采集'}`}
                onPress={() => void act('/touch', { anchor: index })}
                disabled={
                  busy ||
                  session.phase === 'touching' ||
                  session.rows.slice(0, index).some((row) => row === null)
                }
              />
            </View>
          ))}
          {session.capture && (
            <>
              <ThemedText type="small">
                A{session.capture.anchor} · 已收到 {session.capture.samples} 帧 · 剩余{' '}
                {Math.max(0, Math.ceil(session.capture.endsAt - now / 1000))} 秒
              </ThemedText>
              <ActionButton
                label="取消本次采集"
                onPress={() => void act('/cancel')}
                disabled={busy}
              />
            </>
          )}
          {session.anchors && (
            <ThemedText type="small">
              {session.anchors
                .map((xy, index) => `A${index}: (${xy[0].toFixed(2)}, ${xy[1].toFixed(2)}) m`)
                .join('\n')}
              {'\n'}坐标系已建立。点云导入、绕边采集和自动对齐将在下一步接入。
            </ThemedText>
          )}
        </>
      )}
      {error !== '' && (
        <ThemedText type="small" themeColor="textSecondary">
          {error}
        </ThemedText>
      )}
    </View>
  );
}

function ActionButton({
  label,
  onPress,
  disabled,
}: {
  label: string;
  onPress: () => void;
  disabled: boolean;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={{
        padding: Spacing.three,
        borderRadius: 12,
        backgroundColor: theme.tintBackground,
        opacity: disabled ? 0.45 : 1,
      }}
    >
      <ThemedText type="smallBold" style={{ color: theme.tint }}>
        {label}
      </ThemedText>
    </Pressable>
  );
}
