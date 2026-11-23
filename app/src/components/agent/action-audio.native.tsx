/** 原生端录音播放（W10 D5）：复用 WebView 内建 audio 控件（OTA 兼容，无新增原生模块）。 */
import { StyleSheet } from 'react-native';
import WebView from 'react-native-webview';

export function ActionAudio({ url }: { url: string }) {
  const html =
    '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<style>body{margin:0;display:flex;align-items:center;background:transparent}' +
    'audio{width:100%}</style></head><body><audio controls preload="none" src="' +
    url +
    '"></audio></body></html>';
  return (
    <WebView
      source={{ html }}
      originWhitelist={['*']}
      style={styles.webview}
      androidLayerType="software"
    />
  );
}

const styles = StyleSheet.create({ webview: { height: 56, backgroundColor: 'transparent' } });
