/**
 * 助手回复的 Markdown 子集解析（W01 自建渲染层的补齐）：
 * 只处理模型实际产出的语法——**加粗**、`行内代码`、*斜体*、- 无序列表、1. 有序列表、> 引用。
 * 不支持标题语法：聊天文本里「#话题标签」必须保持原样（# 后无空格也不是标题）。
 * 未配对的标记原样保留，解析结果为纯数据，渲染在 markdown-text.tsx。
 */

export type MarkdownSpan = { text: string; bold?: boolean; code?: boolean; italic?: boolean };

export type MarkdownBlock =
  | { kind: 'paragraph'; spans: MarkdownSpan[] }
  | { kind: 'quote'; spans: MarkdownSpan[] }
  | { kind: 'listItem'; marker: string; spans: MarkdownSpan[] };

// 单星号斜体两侧不能紧贴空白或另一个星号，所以「2 * 3 * 4」和「* 列表项」不会被当成斜体。
const INLINE =
  /(\*\*(?:`[^`\n]+`|`(?![^`\n]+`)|[^*`\n])+\*\*|`[^`\n]+`|(?<!\*)\*(?![\s*])[^*\n]*[^\s*]\*(?!\*))/g;
const CODE = /`[^`\n]+`/g;

function parseBold(text: string): MarkdownSpan[] {
  const spans: MarkdownSpan[] = [];
  let offset = 0;
  for (const match of text.matchAll(CODE)) {
    if (match.index > offset) spans.push({ text: text.slice(offset, match.index), bold: true });
    spans.push({ text: match[0].slice(1, -1), bold: true, code: true });
    offset = match.index + match[0].length;
  }
  if (offset < text.length) spans.push({ text: text.slice(offset), bold: true });
  return spans;
}

/** 行内解析：成对的 ** 加粗、`代码`、*斜体* 之外的字符原样保留 */
export function parseInline(line: string, streaming = false): MarkdownSpan[] {
  const spans: MarkdownSpan[] = [];
  let lastIndex = 0;
  for (const match of line.matchAll(INLINE)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) spans.push({ text: line.slice(lastIndex, index) });
    if (token.startsWith('**')) spans.push(...parseBold(token.slice(2, -2)));
    else if (token.startsWith('`')) spans.push({ text: token.slice(1, -1), code: true });
    else spans.push({ text: token.slice(1, -1), italic: true });
    lastIndex = index + token.length;
  }
  let tail = line.slice(lastIndex);
  if (streaming) {
    // 只临时补齐末行的行内样式。完整正文仍按原文解析。
    const open = tail.match(/\*\*(?!\s|\*)([^*`\n]*)\*?$|`([^`\n]*)$/);
    if (open?.index !== undefined) {
      if (open.index > 0) spans.push({ text: tail.slice(0, open.index) });
      if (open[1]) spans.push({ text: open[1], bold: true });
      if (open[2]) spans.push({ text: open[2], code: true });
      return spans;
    }
    tail = tail.replace(/(^|\s)\*$/, '$1');
  }
  if (tail) spans.push({ text: tail });
  return spans.length ? spans : [{ text: line }];
}

const LIST_MARKER = /^(?:[-•*]|\d+\.)\s+/;

const SPEAKER_MARK = /^\s*【(?:爱宠|照料助手)】\s*/;
const SPEAKER_MARKS = ['【爱宠】', '【照料助手】'];

/**
 * 进渲染的正文：去掉开头的发言人标记。气泡上方已有名字标签；流式阶段，以及后台交付的
 * 回合（周期回顾、回信）落库的正文块，都可能还带着「【照料助手】」，不该原样显示（W14 F2）。
 */
export function visibleReplyText(text: string, pending = false): string {
  if (pending && SPEAKER_MARKS.some((mark) => mark.startsWith(text.trim()))) return '';
  return text.replace(SPEAKER_MARK, '');
}

/**
 * 按行分块：连续的普通行合并为一个段落（保留换行），连续的 > 行合并为一个引用块，
 * 列表行逐条成块并保留原标记（避免重新编号）。
 */
export function parseAgentMarkdown(text: string, streaming = false): MarkdownBlock[] {
  if (!text) return [];
  const lines = text.split('\n');
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let quote: string[] = [];

  const parseBlockInline = (block: string, last: boolean) => parseInline(block, streaming && last);

  const flushParagraph = (last = false) => {
    if (paragraph.length) {
      blocks.push({ kind: 'paragraph', spans: parseBlockInline(paragraph.join('\n'), last) });
      paragraph = [];
    }
  };

  const flushQuote = (last = false) => {
    if (quote.length) {
      blocks.push({ kind: 'quote', spans: parseBlockInline(quote.join('\n'), last) });
      quote = [];
    }
  };

  for (const [index, raw] of lines.entries()) {
    const quoteMatch = raw.match(/^>\s?/);
    const listMatch = raw.match(LIST_MARKER);
    if (quoteMatch) {
      flushParagraph();
      quote.push(raw.slice(quoteMatch[0].length));
      continue;
    }
    flushQuote();
    if (!raw.trim()) {
      flushParagraph();
    } else if (listMatch) {
      flushParagraph();
      blocks.push({
        kind: 'listItem',
        // 模型用「* 项」写的无序列表按圆点显示；「-」「•」和编号保留原标记
        marker: listMatch[0].trim() === '*' ? '•' : listMatch[0].trim(),
        spans: parseBlockInline(raw.slice(listMatch[0].length), index === lines.length - 1),
      });
    } else {
      paragraph.push(raw);
    }
  }
  flushParagraph(true);
  flushQuote(true);
  return blocks;
}
