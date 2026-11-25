import { View } from 'react-native';
import { ThemedText } from '@/components/themed-text';
import { regionLabel } from '@/services/region-label';
import type { UwbMap, UwbPosition } from '@/types/uwb';

export function RegionReadout({
  position,
  map,
  fresh,
}: {
  position: UwbPosition | null;
  map: UwbMap;
  fresh: boolean;
}) {
  return (
    <View>
      <ThemedText type="smallBold" accessibilityLiveRegion="polite">
        {regionLabel(position, map, fresh)}
      </ThemedText>
      {position?.position && (
        <ThemedText type="small" themeColor="textSecondary">
          {`${fresh ? '坐标' : '上次坐标'}：X ${position.position.x.toFixed(2)} m · Y ${position.position.y.toFixed(2)} m`}
        </ThemedText>
      )}
    </View>
  );
}
