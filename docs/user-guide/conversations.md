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

## Model And Thinking Controls

Use `/model` to choose the current conversation model. Use `/thinking` to choose
the current thinking mode. Model self-descriptions are not authoritative; use
the UI state and completion metadata to inspect the selected model.
