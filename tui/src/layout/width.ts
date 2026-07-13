import { graphemes } from "../composer/graphemes.js";
import { displayWidth } from "../composer/viewport.js";

export function terminalDisplayWidth(value: string): number {
  return graphemes(value).reduce(
    (total, grapheme) => total + displayWidth(grapheme),
    0,
  );
}
