import { useEffect, useRef } from 'react';
import { Linking, StyleSheet } from 'react-native';
import { WebView } from 'react-native-webview';

import type { DogMapSurfaceProps } from './dog-map-surface.types';

// The base URL and User-Agent identify this embedded map to the tile provider.
const BASE_URL = 'https://github.com/yangeh03/petsense_app/';
export function DogMapSurface({
  html,
  resetRequest,
  focus,
  onMessage,
  onError,
}: DogMapSurfaceProps) {
  const ref = useRef<WebView<unknown>>(null);
  const injectedFocus = useRef(-1);
  const injectedReset = useRef(0);
  useEffect(() => {
    if (resetRequest && resetRequest !== injectedReset.current) {
      injectedReset.current = resetRequest;
      ref.current?.injectJavaScript('window.petSenseDogReset&&window.petSenseDogReset();true;');
    }
  }, [resetRequest]);
  useEffect(() => {
    if (focus && focus.request !== injectedFocus.current) {
      injectedFocus.current = focus.request;
      ref.current?.injectJavaScript(
        `window.petSenseDogFocus&&window.petSenseDogFocus(${focus.index});true;`,
      );
    }
  }, [focus]);
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
      onMessage={({ nativeEvent }) => onMessage(nativeEvent.data)}
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
