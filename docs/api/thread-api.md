# Thread API

The thread API exposes conversation resources for clients. It should mirror the
product contract used by the Local CLI: clients create user message turns and
the runtime owns execution.

## Resources

| Resource | Purpose |
| --- | --- |
| Thread | Conversation container and current user-visible state. |
| Turn | One user message and the assistant/runtime response sequence. |
| Attachment | File context staged for a turn. |
| Run | Runtime execution record behind a turn. |

## Contract

Clients submit plain user messages with optional attachments and configuration
choices. They do not select graph nodes, call providers directly, or execute
tools. Cancellation, retry, and status are runtime operations over existing
resources.

## Related Documents

- [Product surfaces](../architecture/product-surfaces.md)
- [Conversations](../user-guide/conversations.md)
- [Attachments](../user-guide/attachments.md)
