import { describe, expect, it } from "vitest";
import { fitBbox, heuristicMeasureText } from "./fitBbox";

describe("fitBbox", () => {
  it("widens a short original bbox for longer replacement text", () => {
    const original = [10, 10, 40, 25] as const;

    const fitted = fitBbox(
      [...original],
      "LI HUIQI",
      10,
      300,
      200,
      heuristicMeasureText,
    );

    expect(fitted[2]).toBeGreaterThan(original[2]);
    expect(fitted[3] - fitted[1]).toBeGreaterThanOrEqual(
      original[3] - original[1],
    );
  });

  it("wraps at the right page edge and increases height", () => {
    const original = [80, 10, 95, 25] as const;

    const fitted = fitBbox(
      [...original],
      "ABCDEFGHIJ",
      10,
      100,
      200,
      heuristicMeasureText,
    );

    expect(fitted[2]).toBe(100);
    expect(fitted[3] - fitted[1]).toBeGreaterThan(
      original[3] - original[1],
    );
  });

  it("never makes the original bbox smaller", () => {
    const original = [15, 20, 115, 60] as const;

    const fitted = fitBbox(
      [...original],
      "A",
      8,
      200,
      100,
      heuristicMeasureText,
    );

    expect(fitted[2] - fitted[0]).toBeGreaterThanOrEqual(
      original[2] - original[0],
    );
    expect(fitted[3] - fitted[1]).toBeGreaterThanOrEqual(
      original[3] - original[1],
    );
  });

  it("accounts for both CJK and Latin glyph widths", () => {
    const fitted = fitBbox(
      [0, 0, 10, 10],
      "AB中文",
      10,
      200,
      100,
      heuristicMeasureText,
    );

    expect(fitted[2]).toBeCloseTo((2 * 0.55 + 2 * 1.05) * 10 * 1.15);
  });

  it("allocates height for explicit newlines", () => {
    const fitted = fitBbox(
      [10, 10, 100, 20],
      "first\nsecond\nthird",
      10,
      200,
      200,
      heuristicMeasureText,
    );

    expect(fitted[3] - fitted[1]).toBeCloseTo(3 * 10 * 1.2 * 1.15);
  });
});
