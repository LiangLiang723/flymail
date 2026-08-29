const QUOTE_LINE_MARKERS = [
  /^On\s.+\bwrote:\s*$/i,
  /^在.+写道[:：]\s*$/i,
  /^-{3,}\s*(?:Original Message|原始邮件|原邮件)\s*-{3,}$/i,
];

const FROM_HEADER = /^(?:From|发件人)\s*[:：]/i;
const SENT_HEADER = /^(?:Sent|Date|发送时间|日期)\s*[:：]/i;
const TO_HEADER = /^(?:To|收件人)\s*[:：]/i;

function headerBlockStarts(lines: string[], index: number): boolean {
  if (!FROM_HEADER.test(lines[index]?.trim() || '')) return false;
  const window = lines.slice(index, index + 8).map((line) => line.trim());
  return window.some((line) => SENT_HEADER.test(line)) && window.some((line) => TO_HEADER.test(line));
}

export function stripConversationQuotedText(text: string | undefined | null): string {
  const normalized = String(text || '').replace(/\r\n?/g, '\n');
  if (!normalized.trim()) return '';

  const lines = normalized.split('\n');
  let boundary = lines.length;
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (QUOTE_LINE_MARKERS.some((pattern) => pattern.test(trimmed)) || headerBlockStarts(lines, index)) {
      boundary = index;
      break;
    }
  }

  return lines.slice(0, boundary).join('\n').trimEnd();
}

function htmlToQuoteText(html: string): string {
  return html
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/(?:div|p|li|tr|table|h[1-6])\s*>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n[ \t]+/g, '\n')
    .trim();
}

function looksLikeHeaderBlock(html: string): boolean {
  const lines = htmlToQuoteText(html).split('\n').map((line) => line.trim()).filter(Boolean);
  return lines.length > 0 && headerBlockStarts(lines, 0);
}

function earliestIndex(values: number[]): number {
  const candidates = values.filter((value) => value >= 0);
  return candidates.length ? Math.min(...candidates) : -1;
}

export function stripConversationQuotedHtml(html: string | undefined | null): string {
  const source = String(html || '');
  if (!source.trim()) return '';

  const boundaries: number[] = [];
  const structuralMarkers = [
    /<(?:blockquote|div)\b[^>]*\bdata-flymail-quote\s*=\s*["'][^"']*["'][^>]*>/i,
    /<[^>]+\bclass\s*=\s*["'][^"']*\b(?:gmail_quote|yahoo_quoted)\b[^"']*["'][^>]*>/i,
    /<div\b[^>]*\bid\s*=\s*["']divRplyFwdMsg["'][^>]*>/i,
    /<blockquote\b[^>]*\btype\s*=\s*["']cite["'][^>]*>/i,
    /<(?:div|p)\b[^>]*>\s*(?:<[^>]+>\s*)*(?:On\s[\s\S]{0,300}\bwrote:|在[\s\S]{0,300}写道[:：])/i,
  ];
  for (const pattern of structuralMarkers) {
    const match = pattern.exec(source);
    if (match?.index != null) boundaries.push(match.index);
  }

  const hrPattern = /<hr\b[^>]*>/gi;
  for (const match of source.matchAll(hrPattern)) {
    const index = match.index ?? -1;
    if (index < 0) continue;
    const tail = source.slice(index + match[0].length, index + match[0].length + 2500);
    if (looksLikeHeaderBlock(tail)) boundaries.push(index);
  }

  const headerPattern = /<(?:div|p)\b[^>]*>\s*(?:<[^>]+>\s*)*(?:From|发件人)\s*[:：]/gi;
  for (const match of source.matchAll(headerPattern)) {
    const index = match.index ?? -1;
    if (index < 0) continue;
    if (looksLikeHeaderBlock(source.slice(index, index + 2500))) boundaries.push(index);
  }

  const boundary = earliestIndex(boundaries);
  return (boundary >= 0 ? source.slice(0, boundary) : source).trimEnd();
}
