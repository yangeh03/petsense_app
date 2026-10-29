import { useEffect, useRef } from 'react';
import { Linking, StyleSheet } from 'react-native';
import { WebView } from 'react-native-webview';

import type { GpsMapSurfaceProps } from './gps-map-surface.types';

// The base URL and User-Agent identify this embedded map to the tile provider.
const BASE_URL = 'https://github.com/yangeh03/petsense_app/';
export function GpsMapSurface({
  html,
  point,
  centerRequest,
  onMessage,
  onError,
}: GpsMapSurfaceProps) {
  const ref = useRef<WebView<unknown>>(null);
  const { latitude, longitude } = point;
  const sendPosition = () =>
    ref.current?.injectJavaScript(
      `window.petSenseGps&&window.petSenseGps(${JSON.stringify({ latitude, longitude })});true;`,
    );
  useEffect(() => {
    ref.current?.injectJavaScript(
      `window.petSenseGps&&window.petSenseGps(${JSON.stringify({ latitude, longitude })});true;`,
    );
  }, [latitude, longitude]);
  useEffect(() => {
    if (centerRequest)
      ref.current?.injectJavaScript('window.petSenseGpsCenter&&window.petSenseGpsCenter();true;');
  }, [centerRequest]);
  return (
    <WebView<unknown>
      ref={ref}
      style={styles.map}
      source={{ html, baseUrl: BASE_URL }}
      originWhitelist={['*']}
      applicationNameForUserAgent="PetSense/1.0 (+https://github.com/yangeh03/petsense_app)"
      javaScriptEnabled
      geolocationEnabled={false}
      scrollEnabled={false}
      cacheEnabled
      mixedContentMode="never"
      setSupportMultipleWindows={false}
      onMessage={({ nativeEvent }) => {
        if (nativeEvent.data === '{"type":"ready"}') sendPosition();
        onMessage(nativeEvent.data);
      }}
      onError={onError}
      onContentProcessDidTerminate={onError}
      onRenderProcessGone={onError}
      onShouldStartLoadWithRequest={({ url }) => {
        if (url === 'about:blank' || url === BASE_URL) return true;
        if (
          url.startsWith('https://www.openstreetmap.org/copyright') ||
          url === 'https://leafletjs.com/'
        ) {
          void Linking.openURL(url);
        }
        return false;
      }}
    />
  );
}
const styles = StyleSheet.create({ map: { flex: 1 } });
