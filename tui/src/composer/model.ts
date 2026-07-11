export interface ComposerViewport {
  readonly width: number;
  readonly startRow: number;
  readonly rows: readonly string[];
  readonly cursorRow: number;
  readonly cursorColumn: number;
  readonly hiddenAbove: boolean;
  readonly hiddenBelow: boolean;
}

export interface ComposerState {
  readonly value: string;
  readonly cursorGrapheme: number;
  readonly viewport: ComposerViewport;
  readonly history: readonly string[];
  readonly historyIndex: number | null;
  readonly draft: string;
  readonly error?: "input_too_large";
}

export type ComposerAction =
  | { type: "insert"; text: string }
  | { type: "backspace" }
  | { type: "delete" }
  | { type: "left" }
  | { type: "right" }
  | { type: "home" }
  | { type: "end" }
  | { type: "buffer_home" }
  | { type: "buffer_end" }
  | { type: "delete_line_start" }
  | { type: "delete_line_end" }
  | { type: "delete_word" }
  | { type: "replace"; value: string }
  | { type: "resize"; width: number }
  | { type: "submit_history"; value: string }
  | { type: "history_previous" }
  | { type: "history_next" };
