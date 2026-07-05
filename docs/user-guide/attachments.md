# Attachments

Use `/attach <path>` to attach a local file to the next message in the current
conversation.

Pending attachments appear above the input area and are cleared only after the
next turn starts successfully. If turn creation fails before it starts, the
pending attachment remains available for retry.

Attachments are copied into Awesome Agent's local attachment store. They are
not copied into the project directory, not written to memory, and not treated
as generated artifacts. Small UTF-8 text attachments may be injected into the
turn as bounded untrusted context; binary files are exposed as metadata only.

Deleting an attachment removes its stored content, so the content cannot be
downloaded again.
