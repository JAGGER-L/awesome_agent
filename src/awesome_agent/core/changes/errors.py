class ChangeBlobCorrupt(RuntimeError):
    pass


class ChangeCapacityExceeded(RuntimeError):
    pass


class ChangeLifecycleError(RuntimeError):
    pass


class ChangeSetNotFound(LookupError):
    pass


class PendingMutationConflict(RuntimeError):
    pass
