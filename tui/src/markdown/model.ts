export type MarkdownInline =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "strong"; readonly children: readonly MarkdownInline[] }
  | { readonly kind: "emphasis"; readonly children: readonly MarkdownInline[] }
  | { readonly kind: "deleted"; readonly children: readonly MarkdownInline[] }
  | { readonly kind: "inline_code"; readonly text: string }
  | {
      readonly kind: "link";
      readonly href: string;
      readonly children: readonly MarkdownInline[];
    }
  | { readonly kind: "break" };

export type MarkdownNode =
  | {
      readonly kind: "heading";
      readonly depth: number;
      readonly children: readonly MarkdownInline[];
    }
  | {
      readonly kind: "paragraph";
      readonly children: readonly MarkdownInline[];
    }
  | {
      readonly kind: "list";
      readonly ordered: boolean;
      readonly start: number;
      readonly items: readonly (readonly MarkdownInline[])[];
    }
  | { readonly kind: "quote"; readonly children: readonly MarkdownNode[] }
  | {
      readonly kind: "code";
      readonly text: string;
      readonly language?: string;
    }
  | { readonly kind: "rule" };
