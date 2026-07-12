import clipboardy from "clipboardy";

export interface ClipboardAdapter {
  writeText(value: string): Promise<void>;
}

export interface ClipboardWriter {
  write(value: string): Promise<void>;
}

export function createClipboardAdapter(
  writer: ClipboardWriter = clipboardy,
): ClipboardAdapter {
  return {
    async writeText(value) {
      await writer.write(value);
    },
  };
}
