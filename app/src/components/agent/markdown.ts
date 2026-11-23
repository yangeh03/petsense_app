/**
 * 助手回复的 Markdown 子集解析（W01 自建渲染层的补齐）：
 * 只处理模型实际产出的语法——**加粗**、`行内代码`、- 无序列表、1. 有序列表、> 引用。
 * 不支持标题语法：聊天文本里「#话题标签」必须保持原样（# 后无空格也不是标题）。
 * 未配对的标记原样保留，解析结果为纯数据，渲染在 markdown-text.tsx。
 */

export type MarkdownSpan = { text: string; bold?: boolean; code?: boolean };

export type MarkdownBlock =
  | { kind: 'paragraph'; spans: MarkdownSpan[] }
  | { kind: 'quote'; spans: MarkdownSpan[] }
  | { kind: 'listItem'; marker: string; spans: MarkdownSpan[] };

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;

/** 行内解析：成对的 ** 加粗与 `代码` 之外的字符原样保留 */
export function parseInline(line: string): MarkdownSpan[] {
  const spans: MarkdownSpan[] = [];
  let lastIndex = 0;
  for (const match of line.matchAll(INLINE)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) spans.push({ text: line.slice(lastIndex, index) });
    if (token.startsWith('**')) spans.push({ text: token.slice(2, -2), bold: true });
    else spans.push({ text: token.slice(1, -1), code: true });
    lastIndex = index + token.length;
  }
  if (lastIndex < line.length) spans.push({ text: line.slice(lastIndex) });
  return spans.length ? spans : [{ text: line }];
}

const LIST_MARKER = /^(?:[-•]|\d+\.)\s+/;

/**
 * 按行分块：连续的普通行合并为一个段落（保留换行），连续的 > 行合并为一个引用块，
 * 列表行逐条成块并保留原标记（避免重新编号）。
 */
export function parseAgentMarkdown(text: string): MarkdownBlock[] {
  if (!text) return [];
  const lines = text.split('\n');
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: 'paragraph', spans: parseInline(paragraph.join('\n')) });
      paragraph = [];
    }
  };

  for (const raw of lines) {
    const quoteMatch = raw.match(/^>\s?/);
    const listMatch = raw.match(LIST_MARKER);
    if (quoteMatch) {
      flushParagraph();
      const line = raw.slice(quoteMatch[0].length);
      const last = blocks[blocks.length - 1];
      if (last?.kind === 'quote') {
        // 连续引用行合并为一个块，行间换行由渲染层保留
        last.spans = parseInline(last.spans.map((s) => s.text).join('\n') + '\n' + line);
      } else {
        blocks.push({ kind: 'quote', spans: parseInline(line) });
      }
    } else if (listMatch) {
      flushParagraph();
      blocks.push({
        kind: 'listItem',
        marker: listMatch[0].trim(),
        spans: parseInline(raw.slice(listMatch[0].length)),
      });
    } else {
      paragraph.push(raw);
    }
  }
  flushParagraph();
  return blocks;
}
