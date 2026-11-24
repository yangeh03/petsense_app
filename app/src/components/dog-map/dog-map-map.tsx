import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { GPS_TILE_URL } from '@/config';
import { DOG_MAP } from '@/data/dog-map';
import { useTheme } from '@/hooks/use-theme';
import { dogMapDocument } from '@/services/dog-map-document';

import { DogMapSurface } from './dog-map-surface';
import type { DogMapFocus } from './dog-map-surface.types';

/** WebView 宿主：底图加载/失败/复位提示；选中点与离开覆盖范围的状态回传给页面。 */
export function DogMapMap({
  hidden,
  focus,
  selected,
  onSelect,
  onDragging,
}: {
  hidden: boolean;
  focus: DogMapFocus | null;
  selected: number | null;
  onSelect: (index: number) => void;
  onDragging: (dragging: boolean) => void;
}) {
  const theme = useTheme();
  const [attempt, setAttempt] = useState(0);
  const [resetRequest, setResetRequest] = useState(0);
  const [tileState, setTileState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [outside, setOutside] = useState(false);
  useEffect(() => {
    const timeout = setTimeout(
      () => setTileState((old) => (old === 'ready' ? old : 'error')),
      15000,
    );
    return () => {
      clearTimeout(timeout);
      onDragging(false);
    };
  }, [attempt, onDragging]);
  const html = useMemo(
    () =>
      dogMapDocument({
        tint: theme.tint,
        background: theme.backgroundSelected,
        textSecondary: theme.textSecondary,
        tileUrl: GPS_TILE_URL,
        coverage: DOG_MAP.coverage,
        points: DOG_MAP.points.map((point) => ({
          longitude: point.longitude,
          latitude: point.latitude,
          score: point.score,
        })),
      }),
    [theme.tint, theme.backgroundSelected, theme.textSecondary],
  );
  const onMessage = useCallback(
    (data: string) => {
      let message;
      try {
        message = JSON.parse(data);
      } catch {
        return;
      }
      if (!message || typeof message !== 'object') return;
      if (message.type === 'tiles-ready') setTileState('ready');
      if (message.type === 'tile-error') setTileState('error');
      if (message.type === 'drag-start') onDragging(true);
      if (message.type === 'drag-end') onDragging(false);
      if (message.type === 'select' && typeof message.value === 'number') onSelect(message.value);
      if (message.type === 'in-coverage') setOutside(false);
      if (message.type === 'out-of-coverage') setOutside(true);
    },
    [onDragging, onSelect],
  );
  return (
    <View style={[styles.container, hidden && styles.hidden]}>
      <View style={[styles.map, { backgroundColor: theme.backgroundSelected }]}>
        <DogMapSurface
          key={`${attempt}:${theme.tint}`}
          html={html}
          resetRequest={resetRequest}
          focus={focus}
          visible={!hidden}
          onMessage={onMessage}
          onError={() => setTileState('error')}
        />
      </View>
      {tileState !== 'ready' && (
        <View style={[styles.overlay, { backgroundColor: theme.backgroundElement }]}>
          {tileState === 'loading' && <ActivityIndicator color={theme.tint} />}
          <ThemedText type="small" themeColor="textSecondary">
            {tileState === 'error'
              ? '底图暂时无法加载，列表仍可查看全部路段。'
              : '正在加载评分地图…'}
          </ThemedText>
          {tileState === 'error' && (
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setTileState('loading');
                setAttempt(attempt + 1);
              }}
            >
              <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                重试底图
              </ThemedText>
            </Pressable>
          )}
        </View>
      )}
      {tileState === 'ready' && outside && (
        <Pressable
          accessibilityRole="button"
          style={[styles.resetBar, { backgroundColor: theme.backgroundElement }]}
          onPress={() => setResetRequest(resetRequest + 1)}
        >
          <ThemedText type="small" themeColor="textSecondary">
            已离开评分区域
          </ThemedText>
          <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
            回到评分区域
          </ThemedText>
        </Pressable>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  container: { width: '100%' },
  hidden: { display: 'none' },
  map: { height: 320, width: '100%', borderRadius: 16, overflow: 'hidden' },
  overlay: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: 12,
    borderRadius: 12,
    padding: 12,
    gap: 8,
    alignItems: 'flex-start',
  },
  resetBar: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: 12,
    borderRadius: 12,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
});
