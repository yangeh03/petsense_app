import { useEffect, useRef } from 'react';

import type { DogMapSurfaceProps } from './dog-map-surface.types';

export function DogMapSurface({
  html,
  resetRequest,
  focus,
  onMessage,
  onError,
}: DogMapSurfaceProps) {
  const ref = useRef<HTMLIFrameElement>(null);
  const injectedFocus = useRef(-1);
  const injectedReset = useRef(0);
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== ref.current?.contentWindow || typeof event.data !== 'string') return;
      onMessage(event.data);
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [onMessage]);
  useEffect(() => {
    if (resetRequest && resetRequest !== injectedReset.current) {
      injectedReset.current = resetRequest;
      ref.current?.contentWindow?.postMessage({ type: 'dog-reset' }, '*');
    }
  }, [resetRequest]);
  useEffect(() => {
    if (focus && focus.request !== injectedFocus.current) {
      injectedFocus.current = focus.request;
      ref.current?.contentWindow?.postMessage({ type: 'dog-focus', index: focus.index }, '*');
    }
  }, [focus]);
  return (
    <iframe
      ref={ref}
      title="遛狗评分地图"
      srcDoc={html}
      sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      referrerPolicy="strict-origin-when-cross-origin"
      onError={onError}
      style={{ border: 0, width: '100%', height: '100%' }}
    />
  );
}
