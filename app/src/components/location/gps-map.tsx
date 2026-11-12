import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { GPS_TILE_URL } from '@/config';
import { useTheme } from '@/hooks/use-theme';
import { gpsMapDocument } from '@/services/gps-map-document';
import type { GpsPoint } from '@/services/gps-view';

import { GpsMapSurface } from './gps-map-surface';

export function GpsMap({
  point,
  onDragging,
}: {
  point: GpsPoint;
  onDragging: (dragging: boolean) => void;
}) {
  const theme = useTheme();
  const [attempt, setAttempt] = useState(0);
  const [centerRequest, setCenterRequest] = useState(0);
  const [state, setState] = useState('loading');
  useEffect(() => {
    const timeout = setTimeout(() => setState((old) => (old === 'ready' ? old : 'error')), 15000);
    return () => {
      clearTimeout(timeout);
      onDragging(false);
    };
  }, [attempt, onDragging]);
  const html = useMemo(
    () => gpsMapDocument(theme.tint, theme.backgroundSelected, GPS_TILE_URL),
    [theme.tint, theme.backgroundSelected],
  );
  const onError = useCallback(() => setState('error'), []);
  const onMessage = useCallback(
    (data: string) => {
      let message;
      try {
        message = JSON.parse(data);
      } catch {
        return;
      }
      if (!message || typeof message !== 'object') return;
      if (message.type === 'tiles-ready') setState('ready');
      if (message.type === 'tile-error') setState('error');
      if (message.type === 'drag-start') onDragging(true);
      if (message.type === 'drag-end') onDragging(false);
    },
    [onDragging],
  );
  return (
    <View style={styles.container}>
      <View style={[styles.map, { backgroundColor: theme.backgroundSelected }]}>
        <GpsMapSurface
          key={`${attempt}:${theme.tint}`}
          html={html}
          point={point}
          centerRequest={centerRequest}
          onMessage={onMessage}
          onError={onError}
        />
      </View>
      {state !== 'ready' && (
        <View style={styles.status}>
          {state === 'loading' && <ActivityIndicator color={theme.tint} />}
          <ThemedText type="small" themeColor="textSecondary">
            {state === 'error' ? '底图暂时无法加载，经纬度仍可查看。' : '正在加载 GPS 地图…'}
          </ThemedText>
          {state === 'error' && (
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setState('loading');
                setAttempt(attempt + 1);
              }}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                重试底图
              </ThemedText>
            </Pressable>
          )}
        </View>
      )}
      <Pressable accessibilityRole="button" onPress={() => setCenterRequest(centerRequest + 1)}>
        <ThemedText type="smallBold" style={{ color: theme.tint }}>
          回到项圈位置
        </ThemedText>
      </Pressable>
    </View>
  );
}
const styles = StyleSheet.create({
  container: { width: '100%', gap: 12 },
  map: { height: 300, width: '100%', borderRadius: 16, overflow: 'hidden' },
  status: { gap: 8, alignItems: 'flex-start' },
});
