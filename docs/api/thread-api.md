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

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/threads` | Create a conversation thread. |
| `GET` | `/threads` | List threads in a paginated envelope. |
| `GET` | `/threads/{thread_id}` | Read one thread. |
| `GET` | `/threads/{thread_id}/messages` | List thread messages. |
| `POST` | `/threads/{thread_id}/turns/stream` | Start a user-message turn. |
| `POST` | `/threads/{thread_id}/turns/continue/stream` | Continue a paused or waiting turn. |
| `GET` | `/threads/{thread_id}/runs` | List runtime run projections for a thread. |
| `POST` | `/threads/{thread_id}/runs/{run_id}/cancel` | Request cancellation. |
| `POST` | `/threads/{thread_id}/runs/{run_id}/approvals/{approval_id}` | Decide an approval. |

## Pagination

List endpoints return:

```json
{
  "items": [],
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

## Continue Semantics

`continue` resumes the latest resumable run in the thread. When
`expected_run_id` is supplied and that run is no longer the latest resumable
run, the API returns `409` with code `resumable_run_changed`. A client may also
use `after_sequence` to catch up events for the current run after a dropped
stream.

## Related Documents

- [Product surfaces](../architecture/product-surfaces.md)
- [Conversations](../user-guide/conversations.md)
- [Attachments](../user-guide/attachments.md)
