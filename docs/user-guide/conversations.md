# Conversations

Plain user messages are the normal product entry point. Send messages in
`awesome`; the local runtime creates the internal conversation work needed to
answer or modify files.

## Threads

Use `/new` to start a new conversation and `/threads` to switch between
conversations. A conversation remembers its transcript, model settings, and
local project context.

## Continue, Retry, And Cancel

Type `continue` to continue the latest paused or waiting response in the current
conversation. `continue` is a control action and is not sent to the model as a
new user message.

Use `Ctrl+R` to retry the last failed conversation turn. Use `Ctrl+C` to
request cancellation of the active turn.

## Approval Flow

When a tool needs approval, the response pauses and the TUI shows a fixed
approval control. Approving once resumes the same run. Denying continues with a
denied tool result. Cancelling requests run cancellation.

## Tool Timeline

Tool calls are grouped into a timeline so users can scan what happened during a
turn. The collapsed view shows call counts and status counts; the expanded view
shows individual tools and failure details.

## Recovery

If a response pauses, type `continue` to resume the latest resumable turn. If
the active run changed, the product reports a conflict instead of creating a new
user message.

## Model And Thinking Controls

Use `/model` to choose the current conversation model. Use `/thinking` to choose
the current thinking mode. Model self-descriptions are not authoritative; use
the UI state and completion metadata to inspect the selected model.
