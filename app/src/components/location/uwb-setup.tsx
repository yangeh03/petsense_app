import { useEffect, useRef, useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { request } from '@/services/api';
import type { UwbSetupSession as Session } from '@/services/uwb-setup';
import { UwbAlignmentReview, UwbWalkPreview } from '@/components/location/uwb-alignment';

/** Capture runs on the server, so leaving this page does not invent a result. */
export function UwbSetup({
  deviceId,
  onPublished,
  onDragging,
}: {
  deviceId: string;
  onPublished: () => void;
  onDragging: (value: boolean) => void;
}) {
  const theme = useTheme();
  const [session, setSession] = useState<Session | null>(null);
  const [tag, setTag] = useState('0');
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
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
        setActionError('');
        if (action === '/publish') onPublished();
      }
    } catch (reason) {
      if (current === generation.current)
        setActionError(reason instanceof Error ? reason.message : '标定操作失败');
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
                  session.phase === 'walking' ||
                  session.phase === 'processing' ||
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
              {'\n'}坐标系已建立。请保持基站固定，开始绕边采集。
            </ThemedText>
          )}
          {session.anchors && (
            <View style={{ width: '100%', gap: Spacing.three }}>
              <ThemedText type="smallBold">2 · 绕边采集</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                从一个转角附近出发，沿真实场地边缘缓慢走一圈，覆盖所有凹凸转角，回到起点附近后结束。不要长时间停在角落，基站全程保持固定。
              </ThemedText>
              <ActionButton
                label={session.walk ? '重新采集一圈（保留旧记录）' : '开始绕边采集'}
                onPress={() => void act('/walk/start')}
                disabled={busy || session.phase === 'walking' || session.phase === 'processing'}
              />
              {session.walk && (
                <>
                  <UwbWalkPreview session={session} />
                  <ThemedText type="small">
                    有效点 {session.walk.points} · 路程 {session.walk.lengthM.toFixed(1)} m ·
                    首尾距离{' '}
                    {session.walk.closureM == null ? '—' : `${session.walk.closureM.toFixed(2)} m`}
                  </ThemedText>
                  {session.phase === 'walking' && (
                    <ThemedText type="small">
                      {session.walk.ageMs == null || session.walk.ageMs > 2000
                        ? '尚无新鲜定位，请检查三个基站和上传链路'
                        : '正在接收新鲜定位'}{' '}
                      · 无效帧 {session.walk.rejected}
                    </ThemedText>
                  )}
                </>
              )}
              {session.phase === 'walking' && (
                <ActionButton
                  label="已回到起点，结束绕边"
                  onPress={() => void act('/walk/stop')}
                  disabled={busy}
                />
              )}
              <ThemedText type="smallBold">3 · 导入点云并自动对齐</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                用电脑将点云和已确认的地面 /
                尺度参数导入下列后端地址。这里保留原有建图方式；基站坐标由触碰生成，无需手动输入。
              </ThemedText>
              {session.inputs &&
                Object.entries(session.inputs).map(([kind, input]) => (
                  <View key={kind}>
                    <ThemedText type="small">
                      {kind === 'pointcloud'
                        ? '点云 PLY'
                        : kind === 'floor'
                          ? '地面参数 JSON'
                          : '可选：已确认变换 JSON'}{' '}
                      · {input.present ? '已导入' : '等待导入'}
                    </ThemedText>
                    <ThemedText selectable type="small" themeColor="textSecondary">
                      {input.path}
                    </ThemedText>
                  </View>
                ))}
              <ActionButton
                label={
                  session.phase === 'processing' ? '后台正在处理，请稍候…' : '生成俯视图并自动对齐'
                }
                onPress={() => void act('/align')}
                disabled={
                  busy ||
                  !['walk_ready', 'alignment_review', 'published'].includes(session.phase) ||
                  !session.inputs?.floor.present ||
                  !session.inputs?.pointcloud.present
                }
              />
              {session.alignment &&
                session.jobId &&
                ['alignment_review', 'published'].includes(session.phase) && (
                  <UwbAlignmentReview
                    key={session.jobId}
                    deviceId={deviceId}
                    session={session}
                    busy={busy}
                    onDragging={onDragging}
                    onPublish={(candidate, jobId) =>
                      void act('/publish', { candidate, jobId, confirmed: true })
                    }
                  />
                )}
              {session.phase === 'published' && (
                <ThemedText type="small">
                  地图已启用。上方位置图会随新鲜 UWB 数据实时更新；灰点只表示最后位置。
                </ThemedText>
              )}
            </View>
          )}
        </>
      )}
      {error !== '' && (
        <ThemedText type="small" themeColor="textSecondary">
          {error}
        </ThemedText>
      )}
      {actionError !== '' && (
        <ThemedText type="small" themeColor="textSecondary">
          {actionError}
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
