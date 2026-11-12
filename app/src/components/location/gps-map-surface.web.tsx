import { useEffect, useRef } from 'react';

import type { GpsMapSurfaceProps } from './gps-map-surface.types';

export function GpsMapSurface({
  html,
  point,
  centerRequest,
  onMessage,
  onError,
}: GpsMapSurfaceProps) {
  const ref = useRef<HTMLIFrameElement>(null);
  const { latitude, longitude } = point;
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== ref.current?.contentWindow || typeof event.data !== 'string') return;
      if (event.data === '{"type":"ready"}') {
        ref.current?.contentWindow?.postMessage(
          { type: 'gps-position', point: { latitude, longitude } },
          '*',
        );
      }
      onMessage(event.data);
    };
    window.addEventListener('message', handler);
    ref.current?.contentWindow?.postMessage(
      { type: 'gps-position', point: { latitude, longitude } },
      '*',
    );
    return () => window.removeEventListener('message', handler);
  }, [latitude, longitude, onMessage]);
  useEffect(() => {
    if (centerRequest) ref.current?.contentWindow?.postMessage({ type: 'gps-center' }, '*');
  }, [centerRequest]);
  return (
    <iframe
      ref={ref}
      title="项圈 GPS 地图"
      srcDoc={html}
      sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      referrerPolicy="strict-origin-when-cross-origin"
      onError={onError}
      style={{ border: 0, width: '100%', height: '100%' }}
    />
  );
}
