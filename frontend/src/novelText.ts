export type CopyableNovelSegment = {
  title: string;
  body: string;
};

const SCENE_SEPARATOR_PATTERN = /^(?:[*＊·•—\-_=~◆◇※]{3,}|(?:[◆◇※]\s*){1,3})$/u;

export function normalizeNovelParagraphs(value: string): string[] {
  return value
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/^[\s\u3000]+|[\s\u3000]+$/gu, ""))
    .filter(Boolean);
}

export function formatNovelBodyForCopy(value: string): string {
  return normalizeNovelParagraphs(value)
    .map((paragraph) => SCENE_SEPARATOR_PATTERN.test(paragraph) ? paragraph : `　　${paragraph}`)
    .join("\n\n");
}

export function formatNovelChapterForCopy(segment: CopyableNovelSegment, ordinal: number): string {
  const title = segment.title.trim() || `第${ordinal}章`;
  const body = formatNovelBodyForCopy(segment.body);
  return body ? `${title}\n\n${body}` : title;
}

export function formatNovelCollectionForCopy(segments: CopyableNovelSegment[]): string {
  return segments
    .map((segment, index) => formatNovelChapterForCopy(segment, index + 1))
    .join("\n\n\n");
}

export async function copyPlainText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Some browsers expose Clipboard API but reject it outside a secure context.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    if (!document.execCommand("copy")) throw new Error("浏览器拒绝了复制操作");
  } finally {
    textarea.remove();
  }
}
