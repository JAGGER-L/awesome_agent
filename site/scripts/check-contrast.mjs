import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const stylesheet = await readFile(
  join(resolve(scriptDirectory, ".."), "src", "styles", "signal.css"),
  "utf8",
);

function variablesFor(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const body = stylesheet.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`))?.[1];
  if (!body) throw new Error(`Missing theme selector: ${selector}`);
  return Object.fromEntries(
    [...body.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((match) => [
      match[1],
      match[2],
    ]),
  );
}

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/../g)
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground, background) {
  const foregroundLuminance = luminance(foreground);
  const backgroundLuminance = luminance(background);
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
    (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  );
}

const themes = {
  light: variablesFor(":root"),
  dark: variablesFor(':root[data-theme="dark"]'),
};
const contracts = [
  ["light", "--sl-color-gray-3", "--sl-color-bg", 4.5],
  ["light", "--sl-color-accent", "--sl-color-bg", 4.5],
  ["dark", "--sl-color-gray-3", "--sl-color-bg", 4.5],
  ["dark", "--sl-color-accent", "--sl-color-bg", 4.5],
];

const failures = [];
for (const [theme, foregroundName, backgroundName, minimum] of contracts) {
  const foreground = themes[theme][foregroundName];
  const background = themes[theme][backgroundName];
  if (!foreground || !background) {
    failures.push(`${theme}: missing ${foregroundName} or ${backgroundName}`);
    continue;
  }
  const ratio = contrast(foreground, background);
  if (ratio < minimum) {
    failures.push(
      `${theme}: ${foregroundName} on ${backgroundName} is ${ratio.toFixed(2)}:1; ` +
        `expected at least ${minimum}:1`,
    );
  }
}

if (failures.length > 0) {
  console.error("Theme contrast contract failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(`Validated ${contracts.length} theme contrast contracts at WCAG AA.`);
}
