import type { CommandIntent } from "../commands/parser.js";
import type { CommandName } from "../protocol/commands.js";
import type { ComposerAction, ComposerState } from "../composer/model.js";
import type { SurfaceState } from "../state/model.js";

export interface PickerSelection {
  readonly kind?: "selection";
  readonly prompt: string;
  readonly options: readonly {
    readonly value: string;
    readonly label: string;
    readonly description?: string | undefined;
    readonly selected: boolean;
    readonly disabled?: boolean | undefined;
  }[];
}

export interface SecretPrompt {
  readonly kind?: "secret";
  readonly provider: "deepseek" | "kimi" | "mem0" | "tavily";
  readonly action: "add" | "replace";
  readonly label: string;
  readonly environment_variable: string;
  readonly help_url: string;
}
export type PendingInteraction = NonNullable<
  SurfaceState["pending_interaction"]
>;

export type PickerOwner =
  | { readonly kind: "command"; readonly intent: CommandIntent }
  | { readonly kind: "local_theme" }
  | { readonly kind: "thread" }
  | {
      readonly kind: "credential_delete";
      readonly intent: CommandIntent;
      readonly provider: "deepseek" | "kimi" | "mem0" | "tavily";
    }
  | {
      readonly kind: "credential_unverified";
      readonly intent: CommandIntent;
      readonly prompt: SecretPrompt;
      readonly secret: string;
    };

export type UiMode =
  | { readonly kind: "composer" }
  | {
      readonly kind: "workspace_trust";
      readonly workspacePath: string;
      readonly selected: number;
      readonly submitting: boolean;
      readonly message?: string | undefined;
    }
  | {
      readonly kind: "state_reset";
      readonly selected: number;
      readonly submitting: boolean;
      readonly message?: string | undefined;
    }
  | { readonly kind: "fatal"; readonly selected: number }
  | {
      readonly kind: "command_menu";
      readonly query: string;
      readonly selectedCommand?: CommandName;
      readonly viewportStart: number;
    }
  | {
      readonly kind: "picker";
      readonly owner: PickerOwner;
      readonly selection: PickerSelection;
      readonly selected: number;
      readonly blocking: boolean;
      readonly submitting?: boolean;
    }
  | {
      readonly kind: "secret";
      readonly intent: CommandIntent;
      readonly prompt: SecretPrompt;
      readonly value: string;
      readonly submitting: boolean;
      readonly message?: string | undefined;
    }
  | {
      readonly kind: "approval";
      readonly interaction: PendingInteraction;
      readonly selected: number;
      readonly submitting: boolean;
      readonly message?: string | undefined;
    };

export interface TerminalUiState {
  readonly mode: UiMode;
  readonly composer: ComposerState;
  readonly composerSubmitting: boolean;
  readonly detailsExpanded: boolean;
  readonly composerMessage?: string | undefined;
  readonly notice?: string;
}

export type TerminalUiAction =
  | {
      readonly type: "mode.open";
      readonly mode: Exclude<UiMode, { kind: "composer" }>;
    }
  | { readonly type: "mode.cancel" }
  | { readonly type: "mode.select"; readonly delta: -1 | 1 }
  | { readonly type: "mode.set"; readonly selected: number }
  | { readonly type: "mode.secret.insert"; readonly text: string }
  | { readonly type: "mode.secret.backspace" }
  | { readonly type: "mode.secret.submitting"; readonly submitting: boolean }
  | {
      readonly type: "mode.secret.message";
      readonly message?: string | undefined;
    }
  | { readonly type: "mode.approval.submitting"; readonly submitting: boolean }
  | { readonly type: "mode.picker.submitting"; readonly submitting: boolean }
  | {
      readonly type: "mode.approval.message";
      readonly message?: string | undefined;
    }
  | { readonly type: "mode.trust.submitting"; readonly submitting: boolean }
  | {
      readonly type: "mode.trust.message";
      readonly message?: string | undefined;
    }
  | {
      readonly type: "mode.state_reset.submitting";
      readonly submitting: boolean;
    }
  | {
      readonly type: "mode.state_reset.message";
      readonly message?: string | undefined;
    }
  | { readonly type: "composer.edit"; readonly action: ComposerAction }
  | { readonly type: "composer.submitting"; readonly submitting: boolean }
  | { readonly type: "composer.message"; readonly message?: string | undefined }
  | { readonly type: "notice.set"; readonly message: string }
  | { readonly type: "notice.clear" }
  | { readonly type: "details.toggle" };
