import { useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import type { AgentWebSource } from '@/services/agent';

function checkedLabel(checkedAt: string) {
  const date = new Date(checkedAt);
  if (!Number.isFinite(date.getTime())) return '';
  const format = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  return `${format.format(date)} 核对`;
}

export function WebSourceCard({ source }: { source: AgentWebSource }) {
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  let url: URL;
  try {
    url = new URL(source.url);
  } catch {
    return <ThemedText type="small">网页来源地址无效</ThemedText>;
  }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
    return <ThemedText type="small">网页来源地址无效</ThemedText>;
  }
  const title = source.title?.trim() || url.hostname;
  const checked = checkedLabel(source.checkedAt);

  return (
    <View>
      <Pressable
        accessibilityRole="link"
        accessibilityLabel={`打开网页来源，${title}，${url.hostname}`}
        onPress={() => {
          setFailed(false);
          void Linking.openURL(url.href).catch(() => setFailed(true));
        }}
        style={[
          styles.card,
          { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
        ]}
      >
        <SymbolView
          name={{ ios: 'safari', android: 'language', web: 'language' }}
          tintColor={theme.tint}
          size={17}
        />
        <View style={styles.details}>
          <ThemedText type="smallBold" numberOfLines={2}>
            {title}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {checked ? `${url.hostname} · ${checked}` : url.hostname}
          </ThemedText>
        </View>
        <SymbolView
          name={{ ios: 'arrow.up.right', android: 'north_east', web: 'north_east' }}
          tintColor={theme.textSecondary}
          size={13}
        />
      </Pressable>
      {failed ? (
        <ThemedText type="small" accessibilityLiveRegion="polite">
          暂时无法打开网页
        </ThemedText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 10,
  },
  details: { flex: 1, minWidth: 0, gap: 2 },
});
