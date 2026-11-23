/**
 * 助手回复的 Markdown 渲染（解析见 markdown.ts）：加粗 / 行内代码 / 列表 / 引用块。
 * 样式沿用 ThemedText 字阶：正文 16/24，加粗 700，代码等宽，引用块左侧色条 + 次要色文字。
 */

import { StyleSheet, Text, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Fonts } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

import { parseAgentMarkdown, type MarkdownSpan } from './markdown';

function Spans({ spans }: { spans: MarkdownSpan[] }) {
  return (
    <>
      {spans.map((span, index) =>
        span.bold ? (
          <Text key={index} style={styles.bold}>
            {span.text}
          </Text>
        ) : span.code ? (
          <Text key={index} style={styles.code}>
            {span.text}
          </Text>
        ) : (
          <Text key={index}>{span.text}</Text>
        ),
      )}
    </>
  );
}

export function AgentMarkdownText({ text }: { text: string }) {
  const theme = useTheme();
  const blocks = parseAgentMarkdown(text);
  return (
    <View style={styles.column}>
      {blocks.map((block, index) => {
        if (block.kind === 'quote') {
          return (
            <View key={index} style={[styles.quote, { borderLeftColor: theme.backgroundSelected }]}>
              <ThemedText themeColor="textSecondary">
                <Spans spans={block.spans} />
              </ThemedText>
            </View>
          );
        }
        if (block.kind === 'listItem') {
          return (
            <ThemedText key={index} style={styles.listItem}>
              {block.marker} <Spans spans={block.spans} />
            </ThemedText>
          );
        }
        return (
          <ThemedText key={index}>
            <Spans spans={block.spans} />
          </ThemedText>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  column: { gap: 6 },
  bold: { fontWeight: '700' },
  code: { fontFamily: Fonts.mono },
  quote: { borderLeftWidth: 3, paddingLeft: 10 },
  listItem: { paddingLeft: 4 },
});
