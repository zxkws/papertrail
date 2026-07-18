import type { BBox } from "../types";

export type MeasureText = (text: string, fontSizePt: number) => number;

const SAFETY_MARGIN = 1.15;
const LINE_HEIGHT = 1.2;

function isCjk(character: string): boolean {
  const codePoint = character.codePointAt(0) ?? 0;
  return (
    (codePoint >= 0x3400 && codePoint <= 0x9fff) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0x3040 && codePoint <= 0x30ff) ||
    (codePoint >= 0xac00 && codePoint <= 0xd7af)
  );
}

export const heuristicMeasureText: MeasureText = (text, fontSizePt) =>
  Array.from(text).reduce(
    (width, character) =>
      width + fontSizePt * (isCjk(character) ? 1.05 : 0.55),
    0,
  );

export const canvasMeasureText: MeasureText = (text, fontSizePt) => {
  if (typeof document === "undefined")
    return heuristicMeasureText(text, fontSizePt);

  try {
    const context = document.createElement("canvas").getContext("2d");
    if (!context) return heuristicMeasureText(text, fontSizePt);
    context.font = `${fontSizePt}px Helvetica, "Noto Sans CJK SC", "PingFang SC", sans-serif`;
    return context.measureText(text).width;
  } catch {
    return heuristicMeasureText(text, fontSizePt);
  }
};

function wrappedLineCount(
  text: string,
  maxMeasuredWidth: number,
  fontSizePt: number,
  measureText: MeasureText,
): number {
  if (text.length === 0) return 1;

  let lines = 1;
  let current = "";
  for (const character of Array.from(text)) {
    const candidate = current + character;
    if (
      current.length > 0 &&
      measureText(candidate, fontSizePt) > maxMeasuredWidth
    ) {
      lines += 1;
      current = character;
    } else {
      current = candidate;
    }
  }
  return lines;
}

export function fitBbox(
  original: BBox,
  text: string,
  fontSizePt: number,
  pageWidthPt: number,
  pageHeightPt: number,
  measureText: MeasureText = canvasMeasureText,
): BBox {
  const [originalLeft, originalTop, originalRight, originalBottom] = original;
  const originalWidth = originalRight - originalLeft;
  const originalHeight = originalBottom - originalTop;
  const availableWidth = Math.max(originalWidth, pageWidthPt - originalLeft);
  const explicitLines = text.split("\n");
  const widestLine = Math.max(
    0,
    ...explicitLines.map((line) => measureText(line, fontSizePt)),
  );
  const desiredWidth = widestLine * SAFETY_MARGIN;
  const wraps = desiredWidth > availableWidth;
  const width = Math.max(
    originalWidth,
    Math.min(availableWidth, desiredWidth),
  );
  const maxMeasuredWidth = availableWidth / SAFETY_MARGIN;
  const lineCount = wraps
    ? explicitLines.reduce(
        (count, line) =>
          count +
          wrappedLineCount(line, maxMeasuredWidth, fontSizePt, measureText),
        0,
      )
    : explicitLines.length;
  const desiredHeight =
    lineCount * fontSizePt * LINE_HEIGHT * SAFETY_MARGIN;
  const height = Math.max(originalHeight, desiredHeight);

  const right = Math.max(originalRight, originalLeft + width);
  if (originalTop + height <= pageHeightPt)
    return [originalLeft, originalTop, right, originalTop + height];

  const top = Math.max(0, pageHeightPt - height);
  return [originalLeft, top, right, pageHeightPt];
}
