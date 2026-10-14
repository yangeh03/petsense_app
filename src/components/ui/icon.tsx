import type { ComponentProps } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { StyleSheet, View } from 'react-native';

/**
 * 全局图标：MaterialCommunityIcons（对齐参考稿的 Material Symbols 风格）。
 * 统一入口便于后续替换图标族。
 */
export function AppIcon({
  name,
  size = 20,
  color,
}: {
  name: ComponentProps<typeof MaterialCommunityIcons>['name'];
  size?: number;
  color: string;
}) {
  return (
    <View style={styles.container}>
      <MaterialCommunityIcons name={name} size={size} color={color} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
  },
});
