class WorkflowError(Exception):
    pass


class NotFoundError(WorkflowError):
    pass


class ConflictError(WorkflowError):
    pass


class ApprovalRequiredError(WorkflowError):
    pass
