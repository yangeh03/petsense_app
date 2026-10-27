import { StyleSheet, View } from 'react-native';

import AppTabs from '../../components/app-tabs';

/** App-wide navigation stays focused on five owner-facing destinations. */
export default function TabLayout() {
  return (
    <View style={styles.container}>
      <AppTabs />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
});
