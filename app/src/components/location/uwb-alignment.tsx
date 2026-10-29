import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, View } from 'react-native';
import Svg, { Circle, Polyline, Text as SvgText } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { MapViewport } from '@/components/location/map-viewport';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { request } from '@/services/api';
import {
  canPublishPreview,
  type AlignmentPreview,
  type UwbSetupSession,
} from '@/services/uwb-setup';

export function UwbWalkPreview({ session }: { session: UwbSetupSession }) {
  const theme = useTheme();
  const anchors = session.anchors ?? [];
  const trail = session.walk?.trail ?? [];
  const all = [...anchors, ...trail];
  if (!all.length) return null;
  const low = [Math.min(...all.map((p) => p[0])) - 0.4, Math.min(...all.map((p) => p[1])) - 0.4];
  const high = [Math.max(...all.map((p) => p[0])) + 0.4, Math.max(...all.map((p) => p[1])) + 0.4];
  const scale = Math.min(290 / (high[0] - low[0]), 190 / (high[1] - low[1]));
  const point = (xy: number[]) => [15 + (xy[0] - low[0]) * scale, 205 - (xy[1] - low[1]) * scale];
  const last = trail.length ? point(trail[trail.length - 1]) : null;
  return (
    <View style={{ width: '100%' }}>
      <Svg
        viewBox="0 0 320 220"
        width="100%"
        height={220}
        accessibilityLabel="实际采集的 UWB 轨迹，尚未配准到地图"
      >
        <Polyline
          points={trail.map((xy) => point(xy).join(',')).join(' ')}
          fill="none"
          stroke={theme.tint}
          strokeWidth={2}
        />
        {anchors.map((xy, index) => {
          const [x, y] = point(xy);
          return (
            <SvgText key={index} x={x} y={y} fill={theme.text} fontSize={12}>{`A${index}`}</SvgText>
          );
        })}
        {last && (
          <Circle
            cx={last[0]}
            cy={last[1]}
            r={5}
            fill={theme.tint}
            opacity={session.walk?.ageMs != null && session.walk.ageMs < 2000 ? 1 : 0.35}
          />
        )}
      </Svg>
      <ThemedText type="small" themeColor="textSecondary">
        UWB 原坐标轨迹 · 尚未与点云对齐
      </ThemedText>
    </View>
  );
}

export function UwbAlignmentReview({
  deviceId,
  session,
  busy,
  onPublish,
  onDragging,
}: {
  deviceId: string;
  session: UwbSetupSession;
  busy: boolean;
  onPublish: (candidate: string, jobId: string) => void;
  onDragging: (value: boolean) => void;
}) {
  const theme = useTheme();
  const jobId = session.jobId ?? '';
  const [selected, setSelected] = useState(session.alignment?.recommended ?? 'normal');
  const [preview, setPreview] = useState<AlignmentPreview | null>(null);
  const [error, setError] = useState('');
  const [imageStatus, setImageStatus] = useState({ scope: '', ready: false });
  const [retry, setRetry] = useState(0);
  const scope = `${jobId}:${selected}`;
  const handleImageReady = useCallback(
    (ready: boolean) => {
      setImageStatus((old) =>
        old.scope === scope && old.ready === ready ? old : { scope, ready },
      );
    },
    [scope],
  );
  const [settled, setSettled] = useState('');
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    async function load() {
      try {
        const result = await request<AlignmentPreview>(
          `/uwb/setup/preview?deviceId=${encodeURIComponent(deviceId)}&candidate=${selected}&jobId=${jobId}`,
          { signal: controller.signal },
        );
        if (!stopped) {
          if (result.jobId !== jobId || result.candidate.id !== selected)
            throw new Error('预览任务已变化，请重新加载');
          setPreview(result);
          setError('');
        }
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : '预览加载失败');
      } finally {
        clearTimeout(timeout);
        if (!stopped) setSettled(scope);
      }
    }
    void load();
    return () => {
      stopped = true;
      clearTimeout(timeout);
      controller.abort();
    };
  }, [deviceId, selected, jobId, scope, retry]);
  const current = preview?.jobId === jobId && preview.candidate.id === selected ? preview : null;
  const ready =
    canPublishPreview(current, jobId, selected) &&
    settled === scope &&
    !error &&
    imageStatus.scope === scope &&
    imageStatus.ready;
  return (
    <View style={{ width: '100%', gap: Spacing.three }}>
      <ThemedText type="smallBold">4 · 配准预览</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        核对轨迹是否覆盖真实边缘、方向是否正确。对称场地可能存在旋转或镜像歧义；不确定时先保留结果，勿直接启用。
      </ThemedText>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two }}>
        {(session.alignment?.candidates ?? []).map((candidate) => (
          <Pressable
            key={candidate.id}
            accessibilityRole="button"
            onPress={() => setSelected(candidate.id)}
            disabled={busy}
            style={{
              padding: Spacing.three,
              backgroundColor:
                selected === candidate.id ? theme.tintBackground : theme.backgroundSelected,
              borderRadius: 12,
            }}
          >
            <ThemedText type="small">
              {
                {
                  grid: '栅格自动配准',
                  manual: '人工确认变换',
                  mirror: '多边形镜像',
                  normal: '多边形普通',
                }[candidate.id]
              }
              {candidate.id === session.alignment?.recommended ? ' · 推荐' : ''}
            </ThemedText>
          </Pressable>
        ))}
      </View>
      {settled !== scope && <ActivityIndicator color={theme.tint} />}
      {current && (
        <>
          <MapViewport
            key={`${jobId}:${selected}`}
            map={current.map}
            preview
            position={null}
            trail={current.candidate.trail.map(([x, y]) => ({ x, y }))}
            fresh={false}
            onDragging={onDragging}
            onImageReady={handleImageReady}
            onRetry={() => setRetry((old) => old + 1)}
          />
          <ThemedText type="small">
            边界覆盖 {(current.candidate.report.boundary_coverage_ratio * 100).toFixed(1)}% ·
            边界距离中位值 {current.candidate.report.median_edge_error_m?.toFixed(3) ?? '无有效值'}{' '}
            m
          </ThemedText>
          {current.candidate.report.grid && (
            <ThemedText type="small">
              室内包络占比 {(current.candidate.report.grid.inside_indoor_ratio * 100).toFixed(1)}% ·
              尺度 {current.candidate.report.scale?.toFixed(4)} · 尺度搜索不等于独立测量精度
            </ThemedText>
          )}
          {!current.candidate.eligible && (
            <ThemedText type="small">
              此候选未达质量要求，请重新绕边采集，并检查点云尺度和地面参数。
            </ThemedText>
          )}
        </>
      )}
      {!!error && (
        <Pressable accessibilityRole="button" onPress={() => setRetry((old) => old + 1)}>
          <ThemedText type="small">{error} · 点击重试</ThemedText>
        </Pressable>
      )}
      <Pressable
        accessibilityRole="button"
        disabled={busy || !ready}
        onPress={() => onPublish(selected, jobId)}
        style={{
          padding: Spacing.three,
          borderRadius: 12,
          backgroundColor: theme.tintBackground,
          opacity: busy || !ready ? 0.45 : 1,
        }}
      >
        <ThemedText type="smallBold" style={{ color: theme.tint }}>
          {session.phase === 'published' ? '切换为此配准结果' : '确认方向与轮廓，启用此地图'}
        </ThemedText>
      </Pressable>
    </View>
  );
}
