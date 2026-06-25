from enum import StrEnum


class RunMode(StrEnum):
    SOLO = "solo"
    TEAM = "team"


class RunIntent(StrEnum):
    READ_ONLY = "read_only"
    MODIFYING = "modifying"


class DispatchStatus(StrEnum):
    QUEUED = "queued"
    TERMINAL = "terminal"


class WorkspaceState(StrEnum):
    READY = "ready"
    RETAINED = "retained"
    CLEANUP_BLOCKED = "cleanup_blocked"


class IntakeReservationStatus(StrEnum):
    PREPARING = "preparing"
    PUBLISHED = "published"
    ROLLBACK_REQUIRED = "rollback_required"
    ROLLED_BACK = "rolled_back"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"


class AgentKind(StrEnum):
    LEADER = "leader"
    TEAMMATE = "teammate"
    SUBAGENT = "subagent"
    VERIFIER = "verifier"


class AgentStatus(StrEnum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class TodoStatus(StrEnum):
    TODO = "todo"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    REJECTED = "rejected"
    VERIFIED = "verified"
    DONE = "done"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STATUS_CHANGED = "run.status_changed"
    AGENT_CREATED = "agent.created"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    TODO_CREATED = "todo.created"
    TODO_STATUS_CHANGED = "todo.status_changed"
    MESSAGE_CREATED = "message.created"
    MODEL_CALL_CREATED = "model_call.created"
    TOOL_CALL_CREATED = "tool_call.created"
    TOOL_PROGRESS = "tool.progress"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    ARTIFACT_CREATED = "artifact.created"
    VERIFICATION_CREATED = "verification.created"
    MEMORY_OPERATION_CREATED = "memory_operation.created"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ExecutionOrigin(StrEnum):
    CLI = "cli"
    API = "api"
