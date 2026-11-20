import { useCallback, useEffect, useState } from 'react';

import { loadLocationMode, saveLocationMode } from '@/services/location-mode';

import type { LocationMode } from '@/services/location-mode';

/** 显式的室内/室外选择；只跟随用户操作，不读取遥测。 */
export function useLocationMode(): { mode: LocationMode; setMode: (mode: LocationMode) => void } {
  const [mode, setMode] = useState<LocationMode>('indoor');
  useEffect(() => {
    let active = true;
    void loadLocationMode().then((stored) => {
      if (active) setMode(stored);
    });
    return () => {
      active = false;
    };
  }, []);
  const setModeExplicit = useCallback((next: LocationMode) => {
    setMode(next);
    void saveLocationMode(next);
  }, []);
  return { mode, setMode: setModeExplicit };
}
